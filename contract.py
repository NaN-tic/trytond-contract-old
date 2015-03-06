# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import datetime
from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrule, DAILY, WEEKLY, MONTHLY, YEARLY
from itertools import groupby
from sql.aggregate import Max

from trytond.config import config
from trytond.model import Workflow, ModelSQL, ModelView, Model, fields
from trytond.pool import Pool
from trytond.pyson import Eval, Bool
from trytond.transaction import Transaction
from trytond.tools import reduce_ids
from trytond.wizard import Wizard, StateView, StateAction, Button
DIGITS = config.getint('digits', 'unit_price_digits', 4)

__all__ = ['ContractService', 'Contract', 'ContractLine', 'RRuleMixin',
    'ContractConsumption', 'CreateConsumptionsStart', 'CreateConsumptions']


class RRuleMixin(Model):
    _rec_name = 'freq'
    freq = fields.Selection([
        (None, 'None'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
        ], 'Frequency')
    interval = fields.Integer('Interval')

    def _rule2update(self):
        res = {}
        for field in ('freq', 'interval'):
            res[field] = getattr(self, field)
        return res

    def rrule_values(self):
        values = {}
        mappings = {
            'freq': {
                'daily': DAILY,
                'weekly': WEEKLY,
                'monthly': MONTHLY,
                'yearly': YEARLY,
                },
            }
        for field in ('freq', 'interval'):
            value = getattr(self, field)
            if not value:
                continue
            if field in mappings:
                if isinstance(mappings[field], str):
                    values[mappings[field]] = value
                else:
                    value = mappings[field][value]
            values[field] = value
        return values

    @property
    def rrule(self):
        'Returns rrule instance from current values'
        values = self.rrule_values()
        return rrule(**values)


class ContractService(ModelSQL, ModelView):
    'Contract Service'
    __name__ = 'contract.service'

    product = fields.Many2One('product.product', 'Product', required=True,
        domain=[
            ('type', '=', 'service'),
            ])

    def get_rec_name(self, name):
        name = super(ContractService, self).get_rec_name(name)
        return '%s (%s)' % (self.product.rec_name, name)

_STATES = {
    'readonly': Eval('state') != 'draft',
    }
_DEPENDS = ['state']


def todatetime(date):
    return datetime.datetime.combine(date, datetime.datetime.min.time())


class Contract(RRuleMixin, Workflow, ModelSQL, ModelView):
    'Contract'
    __name__ = 'contract'

    company = fields.Many2One('company.company', 'Company', required=True,
        states=_STATES, depends=_DEPENDS)
    currency = fields.Many2One('currency.currency', 'Currency', required=True,
        states=_STATES, depends=_DEPENDS)
    party = fields.Many2One('party.party', 'Party', required=True,
        states=_STATES, depends=_DEPENDS)
    reference = fields.Char('Reference', readonly=True, select=True)
    start_date = fields.Date('Start Date', required=True,
        states=_STATES, depends=_DEPENDS)
    end_date = fields.Date('End Date')
    start_period_date = fields.Date('Start Period Date', required=True,
        states=_STATES, depends=_DEPENDS)
    first_invoice_date = fields.Date('First Invoice Date', states=_STATES,
        depends=_DEPENDS)
    lines = fields.One2Many('contract.line', 'contract', 'Lines',
        context={
            'start_date': Eval('start_date'),
            'end_date': Eval('end_date'),
            },
        depends=['start_date', 'end_date'])
    state = fields.Selection([
            ('draft', 'Draft'),
            ('validated', 'Validated'),
            ('cancel', 'Cancel'),
            ], 'State', required=True, readonly=True)

    @classmethod
    def __setup__(cls):
        super(Contract, cls).__setup__()
        for field_name in ('freq', 'interval'):
            field = getattr(cls, field_name)
            field.states = _STATES
            field.depends = _DEPENDS
        cls._transitions |= set((
                ('draft', 'validated'),
                ('validated', 'cancel'),
                ('draft', 'cancel'),
                ('cancel', 'draft'),
                ))
        cls._buttons.update({
                'draft': {
                    'invisible': Eval('state') != 'cancel',
                    'icon': 'tryton-clear',
                    },
                'validate_contract': {
                    'invisible': Eval('state') != 'draft',
                    'icon': 'tryton-go-next',
                    },
                'cancel': {
                    'invisible': Eval('state') == 'cancel',
                    'icon': 'tryton-cancel',
                    },
                })
        cls._error_messages.update({
                'start_date_not_valid': ('Contract %(contract)s with '
                    'invalid date "%(date)s"'),
                })

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    @staticmethod
    def default_currency():
        Company = Pool().get('company.company')
        if Transaction().context.get('company'):
            company = Company(Transaction().context['company'])
            return company.currency.id

    @staticmethod
    def default_state():
        return 'draft'

    @classmethod
    def set_reference(cls, contracts):
        'Fill the reference field with the contract sequence'
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        Config = pool.get('contract.configuration')

        config = Config(1)
        to_write = []
        for contract in contracts:
            if contract.reference:
                continue
            reference = Sequence.get_id(config.contract_sequence.id)
            to_write.extend(([contract], {
                        'reference': reference,
                        }))
        if to_write:
            cls.write(*to_write)

    @classmethod
    def copy(cls, contracts, default=None):
        if default is None:
            default = {}
        default.setdefault('reference', None)
        default.setdefault('end_date', None)
        return super(Contract, cls).copy(contracts, default=default)

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, contracts):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('validated')
    def validate_contract(cls, contracts):
        cls.set_reference(contracts)

    @classmethod
    @ModelView.button
    @Workflow.transition('cancel')
    def cancel(cls, contracts):
        pass

    def rrule_values(self):
        values = super(Contract, self).rrule_values()
        values['dtstart'] = todatetime(self.start_period_date)
        return values

    def get_invoice_date(self, last_invoice_date):
        last_invoice_date = todatetime(last_invoice_date)
        r = rrule(self.rrule._freq, dtstart=last_invoice_date)
        date = r.after(last_invoice_date)
        return date.date()

    def get_consumptions(self, end_date=None):
        pool = Pool()
        Date = pool.get('ir.date')

        if end_date is None:
            end_date = Date.today()

        end_date = todatetime(end_date)
        consumptions = []

        for line in self.lines:

            start_period_date = self.start_period_date

            last_consumption_date = line.last_consumption_date
            if last_consumption_date:
                last_consumption_date = todatetime(line.last_consumption_date)
                last_consumption_date += relativedelta(days=+1)

            start = start_period_date
            if last_consumption_date:
                start = (last_consumption_date + relativedelta(days=+1)).date()

            end_contract = None
            if end_contract:
                end_contract = todatetime(self.end)
                self.rrule.until = end_contract

            last_invoice_date = line.last_consumption_invoice_date

            for date in self.rrule.between(todatetime(start), end_date):
                date -= relativedelta(days=+1)
                date = date.date()
                invoice_date = last_invoice_date or self.first_invoice_date \
                    or date
                if last_invoice_date:
                    invoice_date = self.get_invoice_date(last_invoice_date)

                finish_date = date
                if end_contract and end_contract <= date:
                    date = end_contract

                start_period = start
                if last_consumption_date is None:
                    start_period = start_period_date
                    start = self.start_date

                consumptions.append(line.get_consumption(start, date,
                        invoice_date, start_period, finish_date))
                date += relativedelta(days=+1)
                start_period = date
                start = date
                last_invoice_date = invoice_date
                last_consumption_date = date
        return consumptions

    @classmethod
    def consume(cls, contracts, date=None):
        'Consume the contracts until date'
        pool = Pool()
        ContractConsumption = pool.get('contract.consumption')

        date += relativedelta(days=+1)  # to support included.
        to_create = []
        for contract in contracts:
            to_create += contract.get_consumptions(date)

        return ContractConsumption.create([c._save_values for c in to_create])

    def check_start_date(self):
        if not hasattr(self, 'rrule'):
            return
        d = self.rrule.after(todatetime(self.start_period_date)).date()
        if self.start_date >= self.start_period_date and self.start_date < d:
            return True
        self.raise_user_error('start_date_not_valid', {
                    'contract': self.rec_name,
                    'date': self.start_date,
                    })

    @classmethod
    def validate(cls, contracts):
        super(Contract, cls).validate(contracts)
        for contract in contracts:
            contract.check_start_date()
            pass


class ContractLine(Workflow, ModelSQL, ModelView):
    'Contract Line'
    __name__ = 'contract.line'

    contract = fields.Many2One('contract', 'Contract', required=True,
        ondelete='CASCADE')
    service = fields.Many2One('contract.service', 'Service')
    name = fields.Char('Name')
    description = fields.Text('Description', required=True)
    unit_price = fields.Numeric('Unit Price', digits=(16, DIGITS),
        required=True)
    last_consumption_date = fields.Function(fields.Date(
            'Last Consumption Date'), 'get_last_consumption_date')
    last_consumption_invoice_date = fields.Function(fields.Date(
            'Last Consumption Date'), 'get_last_consumption_date')

    @staticmethod
    def default_state():
        return 'draft'

    @fields.depends('service', 'unit_price', 'description')
    def on_change_service(self):
        changes = {
            'unit_price': None,
            }
        if self.service:
            changes['name'] = self.service.rec_name
            if not self.unit_price:
                changes['unit_price'] = self.service.product.list_price
            if not self.description:
                changes['description'] = self.service.product.rec_name
        return changes

    @classmethod
    def get_last_consumption_date(cls, lines, name):
        pool = Pool()
        Consumption = pool.get('contract.consumption')
        table = Consumption.__table__()
        cursor = Transaction().cursor

        line_ids = [l.id for l in lines]
        values = dict.fromkeys(line_ids, None)
        cursor.execute(*table.select(table.contract_line,
                    Max(table.end_period_date),
                where=reduce_ids(table.contract_line, line_ids),
                group_by=table.contract_line))
        values.update(dict(cursor.fetchall()))
        return values

    @classmethod
    def get_last_consumption_invoice_date(cls, lines, name):
        pool = Pool()
        Consumption = pool.get('contract.consumption')
        table = Consumption.__table__()
        cursor = Transaction().cursor

        line_ids = [l.id for l in lines]
        values = dict.fromkeys(line_ids, None)
        cursor.execute(*table.select(table.contract_line,
                Max(table.invoice_date),
                where=reduce_ids(table.contract_line, line_ids),
                group_by=table.contract_line))
        values.update(dict(cursor.fetchall()))
        return values

    def get_consumption(self, start_date, end_date, invoice_date, start_period,
            finish_period):
        'Returns the consumption for date date'
        pool = Pool()
        Consumption = pool.get('contract.consumption')
        consumption = Consumption()
        consumption.contract_line = self
        consumption.start_date = start_date
        consumption.end_date = end_date
        consumption.init_period_date = start_period
        consumption.end_period_date = finish_period
        consumption.invoice_date = invoice_date
        return consumption


class ContractConsumption(ModelSQL, ModelView):
    'Contract Consumption'
    __name__ = 'contract.consumption'

    contract_line = fields.Many2One('contract.line', 'Contract Line',
        required=True)
    invoice_line = fields.Many2One('account.invoice.line', 'Invoice Line')
    init_period_date = fields.Date('Start Period Date')
    end_period_date = fields.Date('Finish Period Date')
    start_date = fields.Date('Start Date')
    end_date = fields.Date('End Date')
    invoice_date = fields.Date('Invoice Date')

    @classmethod
    def __setup__(cls):
        super(ContractConsumption, cls).__setup__()
        cls._error_messages.update({
                'missing_account_revenue': ('Product "%(product)s" of '
                    'contract line %(contract_line)s misses a revenue '
                    'account.'),
                'missing_account_revenue_property': ('Contract Line '
                    '"%(contract_line)s" misses an "account revenue" default '
                    'property.'),
                })
        cls._buttons.update({
                'invoice': {
                    'invisible': Bool(Eval('invoice_line')),
                    'icon': 'tryton-go-next',
                    },
                })

    def _get_tax_rule_pattern(self):
        '''
        Get tax rule pattern
        '''
        return {}

    def get_invoice_line(self):
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        Property = pool.get('ir.property')
        Uom = pool.get('product.uom')
        invoice_line = InvoiceLine()
        invoice_line.type = 'line'
        invoice_line.origin = self.contract_line
        invoice_line.company = self.contract_line.contract.company
        invoice_line.currency = self.contract_line.contract.currency
        invoice_line.product = None
        if self.contract_line.service:
            invoice_line.product = self.contract_line.service.product
        invoice_line.description = '%(name)s (%(start)s - %(end)s)' % {
            'name': self.contract_line.description,
            'start': self.start_date,
            'end': self.end_date,
            }
        invoice_line.unit_price = self.contract_line.unit_price
        invoice_line.party = self.contract_line.contract.party
        taxes = []
        if invoice_line.product:
            invoice_line.unit = invoice_line.product.default_uom
            party = invoice_line.party
            pattern = self._get_tax_rule_pattern()
            for tax in invoice_line.product.customer_taxes_used:
                if party.customer_tax_rule:
                    tax_ids = party.customer_tax_rule.apply(tax, pattern)
                    if tax_ids:
                        taxes.extend(tax_ids)
                    continue
                taxes.append(tax.id)
            if party.customer_tax_rule:
                tax_ids = party.customer_tax_rule.apply(None, pattern)
                if tax_ids:
                    taxes.extend(tax_ids)
            invoice_line.account = invoice_line.product.account_revenue_used
            if not invoice_line.account:
                self.raise_user_error('missing_account_revenue', {
                        'contract_line': self.contract_line.rec_name,
                        'product': invoice_line.product.rec_name,
                        })
        else:
            invoice_line.unit = None
            for model in ('product.template', 'product.category'):
                invoice_line.account = Property.get('account_revenue', model)
                if invoice_line.account:
                    break
            if not invoice_line.account:
                self.raise_user_error('missing_account_revenue_property', {
                        'contract_line': self.contract_line.rec_name,
                        })
        invoice_line.taxes = taxes
        invoice_line.invoice_type = 'out_invoice'
        # Compute quantity based on dates
        quantity = ((self.end_date - self.start_date).total_seconds() /
            (self.end_period_date - self.init_period_date).total_seconds())
        rounding = invoice_line.unit.rounding if invoice_line.unit else 1
        invoice_line.quantity = Uom.round(quantity, rounding)
        return invoice_line

    @classmethod
    def _group_invoice_key(cls, line):
        '''
        The key to group invoice_lines by Invoice

        line is a tuple of consumption id and invoice line
        '''
        consumption_id, invoice_line = line
        consumption = cls(consumption_id)
        return (
            ('party', invoice_line.party),
            ('company', invoice_line.company),
            ('currency', invoice_line.currency),
            ('type', invoice_line.invoice_type),
            ('invoice_date', consumption.invoice_date),
            )

    @classmethod
    def _get_invoice(cls, keys):
        pool = Pool()
        Invoice = pool.get('account.invoice')
        Journal = pool.get('account.journal')
        journals = Journal.search([
                ('type', '=', 'revenue'),
                ], limit=1)
        if journals:
            journal, = journals
        else:
            journal = None
        values = dict(keys)
        values['invoice_address'] = values['party'].address_get('invoice')
        invoice = Invoice(**values)
        invoice.journal = journal
        invoice.payment_term = invoice.party.customer_payment_term
        invoice.account = invoice.party.account_receivable
        return invoice

    @classmethod
    @ModelView.button
    def invoice(cls, consumptions):
        pool = Pool()
        Invoice = pool.get('account.invoice')
        lines = {}
        to_write = []
        for consumption in consumptions:
            line = consumption.get_invoice_line()
            if line:
                line.save()
                lines[consumption.id] = line
                to_write.extend(([consumption], {
                            'invoice_line': line.id,
                            }))
        if not lines:
            return
        lines = lines.items()
        lines = sorted(lines, key=cls._group_invoice_key)

        invoices = []
        for key, grouped_lines in groupby(lines, key=cls._group_invoice_key):
            invoice = cls._get_invoice(key)
            invoice.lines = (list(getattr(invoice, 'lines', [])) +
                list(x[1] for x in grouped_lines))
            invoices.append(invoice)

        invoices = Invoice.create([x._save_values for x in invoices])
        Invoice.update_taxes(invoices)
        cls.write(*to_write)


class CreateConsumptionsStart(ModelView):
    'Create Consumptions Start'
    __name__ = 'contract.create_consumptions.start'
    date = fields.Date('Date')

    @staticmethod
    def default_date():
        Date = Pool().get('ir.date')
        return Date.today()


class CreateConsumptions(Wizard):
    'Create Consumptions'
    __name__ = 'contract.create_consumptions'
    start = StateView('contract.create_consumptions.start',
        'contract.create_consumptions_start_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('OK', 'create_consumptions', 'tryton-ok', True),
            ])
    create_consumptions = StateAction('contract.act_contract_consumption')

    def do_create_consumptions(self, action):
        pool = Pool()
        Contract = pool.get('contract')
        contracts = Contract.search([
                ('state', '=', 'validated'),
                ])
        consumptions = Contract.consume(contracts, self.start.date)
        data = {'res_id': [c.id for c in consumptions]}
        if len(consumptions) == 1:
            action['views'].reverse()
        return action, data