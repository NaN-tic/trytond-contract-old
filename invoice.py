# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.model import ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval
from trytond.transaction import Transaction
from trytond.wizard import Wizard, StateView, StateAction, Button

__all__ = ['InvoiceLine', 'CreateInvoicesStart', 'CreateInvoices',
    'CreditInvoiceStart', 'CreditInvoice']
__metaclass__ = PoolMeta


class InvoiceLine:
    __name__ = 'account.invoice.line'

    @classmethod
    def _get_origin(cls):
        models = super(InvoiceLine, cls)._get_origin()
        models.append('contract.consumption')
        return models


class CreateInvoicesStart(ModelView):
    'Create Invoices Start'
    __name__ = 'contract.create_invoices.start'
    date = fields.Date('Date', required=True)

    @staticmethod
    def default_date():
        Date = Pool().get('ir.date')
        return Date.today()


class CreateInvoices(Wizard):
    'Create Invoices'
    __name__ = 'contract.create_invoices'
    start = StateView('contract.create_invoices.start',
        'contract.create_invoices_start_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('OK', 'create_invoices', 'tryton-ok', True),
            ])
    create_invoices = StateAction(
            'account_invoice.act_invoice_out_invoice_form')

    def do_create_invoices(self, action):
        pool = Pool()
        Consumptions = pool.get('contract.consumption')
        consumptions = Consumptions.search(
            [('invoice_date', '<=', self.start.date)])
        invoices = Consumptions._invoice(consumptions)

        data = {'res_id': [c.id for c in invoices]}
        if len(invoices) == 1:
            action['views'].reverse()
        return action, data


class CreditInvoiceStart:
    __name__ = 'account.invoice.credit.start'
    from_contract = fields.Boolean('From Contract', readonly=True)
    reinvoice_contract = fields.Boolean('Reinvoice Contract',
        states={
            'invisible': ~Bool(Eval('from_contract')),
            },
        depends=['from_contract'],
        help=('If true, the consumption that generated this line will be '
            'reinvoiced.'))

    @classmethod
    def view_attributes(cls):
        states = {'invisible': ~Bool(Eval('from_contract'))}
        return [
            ('/form//image[@name="tryton-dialog-warning"]', 'states', states),
            ('/form//label[@id="credit_contract"]', 'states', states),
            ]



class CreditInvoice:
    __name__ = 'account.invoice.credit'

    def default_start(self, fields):
        pool = Pool()
        Invoice = pool.get('account.invoice')
        Consumption = pool.get('contract.consumption')

        default = super(CreditInvoice, self).default_start(fields)
        default.update({
            'from_contract': False,
            'reinvoice_contract': False,
            })
        for invoice in Invoice.browse(Transaction().context['active_ids']):
            for line in invoice.lines:
                if isinstance(line.origin, Consumption):
                    default['from_contract'] = True
                    break
        return default

    def do_credit(self, action):
        pool = Pool()
        Invoice = pool.get('account.invoice')
        Consumption = pool.get('contract.consumption')
        transaction = Transaction()

        action, data = super(CreditInvoice, self).do_credit(action)
        if self.start.reinvoice_contract:
            consumptions = set([])
            for invoice in Invoice.browse(transaction.context['active_ids']):
                for line in invoice.lines:
                    if isinstance(line.origin, Consumption):
                        consumptions.add(line.origin)
            with transaction.set_context(force_reinvoice=True):
                Consumption.invoice(list(consumptions))
        return action, data
