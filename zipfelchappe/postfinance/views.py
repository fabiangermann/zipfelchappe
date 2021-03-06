"""
Payment module for Postfinance integration

Needs the following settings to work correctly::

    POSTFINANCE = {
        'PSPID': 'your_shop_id',
        'LIVE': True, # Or False
        'SHA1_IN': 'yourhash',
        'SHA1_OUT': 'yourotherhash',
        }
"""
from __future__ import absolute_import
from hashlib import sha1
import logging

from django.http import HttpResponse, HttpResponseForbidden
from django.utils.translation import get_language, to_locale
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render

from feincms.content.application.models import app_reverse

from zipfelchappe.views import requires_pledge
from zipfelchappe.models import Pledge

from ..app_settings import ROOT_URLS
from .app_settings import POSTFINANCE
from .models import Payment

logger = logging.getLogger('zipfelchappe.postfinance.ipn')
api_logger = logging.getLogger('zipfelchappe.postfinance.api')


@requires_pledge
def payment(request, pledge):

    order_id = '{project_slug}-{pledge_id}'.format(
        project_slug=pledge.project.slug, pledge_id=pledge.id)

    amount = int(pledge.amount * 100)

    form_params = {
        'orderID': order_id,
        'amount': unicode(amount),
        'currency': pledge.currency,
        'PSPID': POSTFINANCE['PSPID'],
        'mode': 'prod' if POSTFINANCE['LIVE'] else 'test',
    }

    form_params['SHASign'] = sha1(u''.join((
        form_params['orderID'],
        form_params['amount'],
        form_params['currency'],
        form_params['PSPID'],
        POSTFINANCE['SHA1_IN'],
    ))).hexdigest()

    # TODO: check why error URLs are not used.
    base_url = 'http://%s' % request.get_host()
    accept_url = base_url + app_reverse('zipfelchappe_pledge_thankyou', ROOT_URLS)
    # decline_url = base_url + reverse('zipfelchappe_postfinance_declined')
    # exception_url = base_url + reverse('zipfelchappe_postfinance_exception')
    cancel_url = base_url + app_reverse('zipfelchappe_pledge_cancel', ROOT_URLS)

    return render(request, 'zipfelchappe/postfinance_form.html', {
        'pledge': pledge,
        'form_params': form_params,
        'locale': to_locale(get_language()),
        'accept_url': accept_url,
        'decline_url': '',
        'exception_url': '',
        'cancel_url': cancel_url,
    })

# TODO: require_POST
def payment_declined(request):
    api_logger.debug({'get': request.GET, 'post': request.POST})
    order_id = request.GET.get('ORDERID', '')
    status = request.GET.get('STATUS', '')
    # TODO: mark pledge as FAILED
    request.session.delete('pledge_id')
    return render(request, 'zipfelchappe/postfinance_declined.html', {
        'order_id': order_id,
        'status': status
    })

# TODO: require_POST
def payment_exception(request):
    api_logger.debug({'get': request.GET, 'post': request.POST})
    order_id = request.GET.get('ORDERID', '')
    status = request.GET.get('STATUS', '')
    # TODO: mark pledge as FAILED
    request.session.delete('pledge_id')
    return render(request, 'zipfelchappe/postfinance_exception.html', {
        'order_id': order_id,
        'status': status
    })

# TODO: require_POST
@csrf_exempt
def ipn(request):
    try:
        parameters_repr = repr(request.POST.copy()).encode('utf-8')
        api_logger.info('IPN: Processing request data %s: %s' % (request.method, parameters_repr))
        try:
            orderID = request.POST['orderID']
            amount = request.POST['amount']
            currency = request.POST['currency']
            PM = request.POST['PM']
            ACCEPTANCE = request.POST['ACCEPTANCE']
            STATUS = request.POST['STATUS']
            CARDNO = request.POST['CARDNO']
            PAYID = request.POST['PAYID']
            NCERROR = request.POST['NCERROR']
            BRAND = request.POST['BRAND']
            SHASIGN = request.POST['SHASIGN']
        except KeyError:
            logger.error('IPN: Missing data in %s' % parameters_repr)
            return HttpResponseForbidden('Missing data')

        sha1_source = u''.join((
            orderID,
            currency,
            amount,
            PM,
            ACCEPTANCE,
            STATUS,
            CARDNO,
            PAYID,
            NCERROR,
            BRAND,
            POSTFINANCE['SHA1_OUT'],
        ))

        sha1_out = sha1(sha1_source).hexdigest()

        if sha1_out.lower() != SHASIGN.lower():
            logger.error('IPN: Invalid hash in %s' % parameters_repr)
            return HttpResponseForbidden('Hash did not validate')

        try:
            # FIXME: Projekte, die ein '-' im Slug haben, schlagen hier fehl.
            project_slug, pledge_id = orderID.split('-')
        except ValueError:
            logger.error('IPN: Error getting pledge id from %s' % orderID)
            return HttpResponseForbidden('Malformed order ID')

        try:
            pledge = Pledge.objects.get(pk=pledge_id)
        except Pledge.DoesNotExist:
            logger.error('IPN: Pledge %s does not exist' % pledge_id)
            return HttpResponseForbidden('Pledge %s does not exist' % pledge_id)


        # save status to database
        p, created = Payment.objects.get_or_create(
            order_id=orderID, pledge=pledge)
        p.amount = amount
        p.currency = currency
        p.STATUS = STATUS
        p.PAYID = PAYID
        p.PM = PM
        p.ACCEPTANCE = ACCEPTANCE
        p.CARDNO = CARDNO
        p.BRAND = BRAND
        p.save()

        logger.info('IPN: Status = %s' % STATUS)
        if STATUS == '5':
            pledge.status = Pledge.AUTHORIZED
        if STATUS == '9':
            pledge.status = Pledge.PAID

        pledge.save()
        logger.info('IPN: Successfully processed IPN request for %s' % orderID)
        return HttpResponse('OK')
    except Exception, e:
        logger.error('IPN: Processing failure %s' % unicode(e))
        raise
