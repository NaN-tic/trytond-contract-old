# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import datetime
from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrule, DAILY, WEEKLY, MONTHLY, YEARLY
from itertools import groupby
from sql import Column, Null, Literal
from sql.conditionals import Case
from sql.aggregate import Max, Min, Sum
from decimal import Decimal

from trytond.model import Workflow, ModelSQL, ModelView, Model, fields
from trytond.pool import Pool
from trytond.pyson import Eval, Bool, If
from trytond.transaction import Transaction
from trytond.tools import reduce_ids, grouped_slice
from trytond.wizard import Wizard, StateView, StateAction, Button
from trytond.modules.product import price_digits

__all__ = ['ContractService', 'Contract', 'ContractLine', 'RRuleMixin',
    'ContractConsumption', 'CreateConsumptionsStart', 'CreateConsumptions']

_STATES = {
    'readonly': Eval('state') != 'draft',
    }
_DEPENDS = ['state']


def todatetime(date):
    return datetime.datetime.combine(date, datetime.datetime.min.time())


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

    name = fields.Char('Name', required=True)
    product = fields.Many2One('product.product', 'Product', required=True,
        domain=[
            ('type', '=', 'service'),
            ])

    @fields.depends('product', 'name')
    def on_change_product(self):
        if not self.name:
            self.name = self.product.rec_name


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
    start_date = fields.Function(fields.Date('Start Date'),
            'get_dates', searcher='search_dates')
    end_date = fields.Function(fields.Date('End Date'),
            'get_dates', searcher='search_dates')
    start_period_date = fields.Date('Start Period Date', required=True,
        states=_STATES, depends=_DEPENDS)
    lines = fields.One2Many('contract.line', 'contract', 'Lines',
        states={
            'readonly': Eval('state') == 'cancel',
            },
        depends=['state'])
    state = fields.Selection([
            ('draft', 'Draft'),
            ('validated', 'Validated'),
            ('cancel', 'Cancel'),
            ('finished', 'Finished'),
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
                ('draft', 'finished'),
                ('validated', 'cancel'),
                ('validated', 'finished'),
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
                'finish_contract': {
                    'invisible': Eval('state') != 'draft',
                    'icon': 'tryton-go-next',
                },
                'create_consumptions': {
                    'invisible': Eval('state') != 'validated',
                    'icon': 'tryton-ok',
                    },
                'cancel': {
                    'invisible': Eval('state') == 'cancel',
                    'icon': 'tryton-cancel',
                    },
                })

    def _get_rec_name(self, name):
        rec_name = []
        if self.reference:
            rec_name.append(self.reference)
        if self.party:
            rec_name.append(self.party.rec_name)
        return rec_name

    def get_rec_name(self, name):
        rec_name = self._get_rec_name(name)
        return "/".join(rec_name)

    @classmethod
    def search_rec_name(cls, name, clause):
        return ['OR',
            ('reference',) + tuple(clause[1:]),
            ('party.name',) + tuple(clause[1:]),
            ]

    @classmethod
    def get_dates(cls, contracts, names):
        pool = Pool()
        ContractLine = pool.get('contract.line')
        cursor = Transaction().cursor
        line = ContractLine.__table__()
        result = {}
        contract_ids = [c.id for c in contracts]
        for name in names:
            if name not in ('start_date', 'end_date'):
                raise Exception('Bad argument')
            result[name] = dict((c, None) for c in contract_ids)
        for sub_ids in grouped_slice(contract_ids):
            where = reduce_ids(line.contract, sub_ids)
            for name in names:
                cursor.execute(*line.select(line.contract,
                        cls._compute_date_column(line, name),
                        where=where,
                        group_by=line.contract))
                for contract, value in cursor.fetchall():
                    # SQlite returns unicode for dates
                    if isinstance(value, unicode):
                        value = datetime.date(*map(int, value.split('-')))
                    result[name][contract] = value
        return result

    @staticmethod
    def _compute_date_column(table, name):
        func = Min if name == 'start_date' else Max
        column = Column(table, name)
        sum_ = Sum(Case((column == Null, Literal(1)), else_=Literal(0)))
        # As fields can be null, return null if at least one of them is null
        return Case((sum_ >= Literal(1), Null), else_=func(column))

    @classmethod
    def search_dates(cls, name, clause):
        pool = Pool()
        ContractLine = pool.get('contract.line')
        line = ContractLine.__table__()
        Operator = fields.SQL_OPERATORS[clause[1]]
        query = line.select(line.contract, group_by=line.contract,
                having=Operator(cls._compute_date_column(line, name),
                clause[2]))
        return [('id', 'in', query)]

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
    def create_consumptions(cls, contracts):
        Date = Pool().get('ir.date')

        validated = []
        for c in contracts:
            if c.state == 'validated' and c.freq != None:
                validated.append(c)

        if validated:
            cls.consume(validated, Date.today())

    @classmethod
    @ModelView.button
    @Workflow.transition('cancel')
    def cancel(cls, contracts):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('finished')
    def finish_contract(cls, contracts):
        pass

    def rrule_values(self):
        values = super(Contract, self).rrule_values()
        values['dtstart'] = todatetime(self.start_period_date)
        return values

    def get_invoice_date(self, last_invoice_date):
        last_invoice_date = todatetime(last_invoice_date)
        r = rrule(self.rrule._freq, interval=self.rrule._interval,
            dtstart=last_invoice_date)
        date = r.after(last_invoice_date)
        return date.date()

    def get_start_period_date(self, start_date):
        r = rrule(self.rrule._freq, interval=self.rrule._interval,
            dtstart=self.start_period_date)
        date = r.before(todatetime(start_date), inc=True)
        if date:
            return date.date()
        return self.start_period_date

    def get_consumptions(self, end_date=None):
        pool = Pool()
        Date = pool.get('ir.date')

        if end_date is None:
            end_date = Date.today()

        consumptions = []

        for line in self.lines:
            start_period_date = self.get_start_period_date(line.start_date)

            last_consumption_date = line.last_consumption_date
            if last_consumption_date:
                last_consumption_date = todatetime(line.last_consumption_date)

            start = start_period_date
            if last_consumption_date:
                start = (last_consumption_date + relativedelta(days=+1)).date()

            end_contract = line.end_date or self.end_date
            if end_contract:
                self.rrule.until = end_contract

            last_invoice_date = line.last_consumption_invoice_date

            next_period = self.rrule.after(todatetime(start)) + \
                relativedelta(days=+1)

            if end_contract and next_period.date() < end_contract:
                next_period = todatetime(end_contract)

            for date in self.rrule.between(todatetime(start), next_period):
                date -= relativedelta(days=+1)
                date = date.date()
                invoice_date = last_invoice_date or line.first_invoice_date \
                    or date
                if last_invoice_date:
                    invoice_date = self.get_invoice_date(last_invoice_date)

                if invoice_date > end_date:
                    break

                finish_date = date
                if end_contract and end_contract <= date:
                    date = end_contract

                start_period = start
                if last_consumption_date is None:
                    start_period = start_period_date
                    start = line.start_date or self.start

                if ((not end_contract or (invoice_date <= end_contract) or
                            (finish_date <= end_contract) or
                            (invoice_date < end_date))
                        and not start > date):
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

    @classmethod
    def generate_contract_consumptions(cls):
        'Generate Contract Consumptions'
        Date = Pool().get('ir.date')

        contracts = cls.search([
                ('state', '=', 'validated'),
                ('freq', '!=', None),
                ])
        cls.consume(contracts, Date.today())


class ContractLine(ModelSQL, ModelView):
    'Contract Line'
    __name__ = 'contract.line'

    contract = fields.Many2One('contract', 'Contract', required=True,
        ondelete='CASCADE')
    service = fields.Many2One('contract.service', 'Service')
    start_date = fields.Date('Start Date', required=True,
        domain=[
            If(Bool(Eval('end_date')),
                ('start_date', '<=', Eval('end_date', None)),
                ()),
            ],
        depends=['end_date'])
    end_date = fields.Date('End Date',
        domain=[
            If(Bool(Eval('end_date')),
                ('end_date', '>=', Eval('start_date', None)),
                ()),
            ],
        depends=['start_date'])
    description = fields.Text('Description', required=True)
    unit_price = fields.Numeric('Unit Price', digits=price_digits,
        required=True)
    last_consumption_date = fields.Function(fields.Date(
            'Last Consumption Date'), 'get_last_consumption_date')
    last_consumption_invoice_date = fields.Function(fields.Date(
            'Last Invoice Date'), 'get_last_consumption_invoice_date')
    consumptions = fields.One2Many('contract.consumption', 'contract_line',
        'Consumptions', readonly=True)
    first_invoice_date = fields.Date('First Invoice Date', required=True)
    sequence = fields.Integer('Sequence')

    @classmethod
    def __setup__(cls):
        super(ContractLine, cls).__setup__()
        cls._order = [('contract', 'ASC'), ('sequence', 'ASC')]

    @staticmethod
    def order_sequence(tables):
        table, _ = tables[None]
        return [table.sequence == None, table.sequence]

    def get_rec_name(self, name):
        rec_name = self.contract.rec_name
        if self.service:
            rec_name = '%s (%s)' % (self.service.rec_name, rec_name)
        return rec_name

    @classmethod
    def search_rec_name(cls, name, clause):
        return ['OR',
            ('contract.rec_name',) + tuple(clause[1:]),
            ('service.rec_name',) + tuple(clause[1:]),
            ]

    @classmethod
    def copy(cls, lines, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['consumptions'] = None
        return super(ContractLine, cls).copy(lines, default=default)

    @fields.depends('service', 'unit_price', 'description', 'contract')
    def on_change_service(self):
        Product = Pool().get('product.product')

        if self.service:
            self.name = self.service.rec_name
            if not self.unit_price:
                self.unit_price = self.service.product.list_price
            if not self.description:
                lang = Transaction().context.get('language')
                product = self.service.product

                party_context = {}
                if self.contract and self.contract.party:
                    party = self.contract.party
                    if party.lang:
                        party_context['language'] = party.lang.code

                    # reload product and change lang because
                    # party lang and user lang are different
                    if party_context.get('language') and \
                            (lang != party_context['language']):
                        with Transaction().set_context(party_context):
                            self.description = Product(product.id).rec_name
                if not self.description:
                    self.description = product.rec_name

    @fields.depends('start_date', 'first_invoice_date')
    def on_change_start_date(self):
        if self.start_date and not self.first_invoice_date:
            self.first_invoice_date = self.start_date

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
    init_period_date = fields.Date('Start Period Date', required=True,
        domain=[
            ('init_period_date', '<=', Eval('end_period_date', None)),
            ],
        depends=['end_period_date'])
    end_period_date = fields.Date('Finish Period Date', required=True,
        domain=[
            ('end_period_date', '>=', Eval('init_period_date', None)),
            ],
        depends=['init_period_date'])
    start_date = fields.Date('Start Date', required=True,
        domain=[
            ('start_date', '<=', Eval('end_date', None)),
            ],
        depends=['end_date'])
    end_date = fields.Date('End Date', required=True,
        domain=[
            ('end_date', '>=', Eval('start_date', None)),
            ],
        depends=['start_date'])
    invoice_date = fields.Date('Invoice Date', required=True)
    invoice_lines = fields.One2Many('account.invoice.line', 'origin',
        'Invoice Lines', readonly=True)
    credit_lines = fields.Function(fields.One2Many('account.invoice.line',
            None, 'Credit Lines',
            states={
                'invisible': ~Bool(Eval('credit_lines')),
                }),
        'get_credit_lines')
    contract = fields.Function(fields.Many2One('contract',
        'Contract'), 'get_contract', searcher='search_contract')

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
                'delete_invoiced_consumption': ('Consumption "%s" can not be'
                    ' deleted because it is already invoiced.'),
                })
        cls._buttons.update({
                'invoice': {
                    'icon': 'tryton-go-next',
                    },
                })

    def get_credit_lines(self, name):
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        return [x.id for x in InvoiceLine.search([
                    ('origin.id', 'in', [l.id for l in self.invoice_lines],
                        'account.invoice.line')])]

    def get_contract(self, name=None):
        return self.contract_line.contract.id

    @classmethod
    def search_contract(cls, name, clause):
        return [('contract_line.contract',) + tuple(clause[1:])]

    def _get_tax_rule_pattern(self):
        '''
        Get tax rule pattern
        '''
        return {}

    def _get_start_end_date(self):
        pool = Pool()
        Lang = pool.get('ir.lang')
        if self.contract.party.lang:
            lang = self.contract.party.lang
        else:
            language = Transaction().language
            languages = Lang.search([('code', '=', language)])
            if not languages:
                languages = Lang.search([('code', '=', 'en_US')])
            lang = languages[0]
        start = Lang.strftime(self.start_date,
            lang.code, lang.date)
        end = Lang.strftime(self.end_date, lang.code,
            lang.date)
        return start, end

    def get_invoice_line(self):
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        Property = pool.get('ir.property')
        Uom = pool.get('product.uom')
        if (self.invoice_lines and
                not Transaction().context.get('force_reinvoice', False)):
            return
        invoice_line = InvoiceLine()
        invoice_line.type = 'line'
        invoice_line.origin = self
        invoice_line.company = self.contract_line.contract.company
        invoice_line.currency = self.contract_line.contract.currency
        invoice_line.sequence = self.contract_line.sequence
        invoice_line.product = None
        if self.contract_line.service:
            invoice_line.product = self.contract_line.service.product
        start_date, end_date = self._get_start_end_date()
        invoice_line.description = '[%(start)s - %(end)s] %(name)s' % {
            'start': start_date,
            'end': end_date,
            'name': self.contract_line.description,
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
        invoice_line.invoice_type = 'out'
        if self.end_period_date == self.init_period_date:
            quantity = 0.0
        else:
            # Compute quantity based on dates
            quantity = ((self.end_date - self.start_date).total_seconds() /
                (self.end_period_date - self.init_period_date).total_seconds())
        rounding = invoice_line.unit.rounding if invoice_line.unit else 1
        invoice_line.quantity = Uom.round(quantity, rounding)
        return invoice_line

    def get_amount_to_invoice(self):
        pool = Pool()
        Uom = pool.get('product.uom')
        quantity = ((self.end_date - self.start_date).total_seconds() /
            (self.end_period_date - self.init_period_date).total_seconds())

        uom = self.contract_line and self.contract_line.service and \
            self.contract_line.service.product and \
            self.contract_line.service.product.default_uom
        rounding = uom.rounding if uom else 1
        qty = Uom.round(quantity, rounding)
        return Decimal(str(qty)) * self.contract_line.unit_price

    @classmethod
    def _group_invoice_key(cls, line):
        '''
        The key to group invoice_lines by Invoice

        line is a tuple of consumption id and invoice line
        '''
        consumption_id, invoice_line = line
        consumption = cls(consumption_id)
        grouping = [
            ('party', invoice_line.party),
            ('company', invoice_line.company),
            ('currency', invoice_line.currency),
            ('type', invoice_line.invoice_type),
            ('invoice_date', consumption.invoice_date),
            ]
        if invoice_line.party.contract_grouping_method is None:
            grouping.append(('contract', consumption.contract_line.contract))
        return grouping

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
        # Compatibility with account_payment_type module
        if hasattr(Invoice, 'payment_type'):
            invoice.payment_type = invoice.party.customer_payment_type
            # Compatibility with account_bank module
            if invoice.payment_type and hasattr(Invoice, 'bank_account'):
                invoice._get_bank_account()
        return invoice

    @classmethod
    @ModelView.button
    def invoice(cls, consumptions):
        cls._invoice(consumptions)

    @classmethod
    def _invoice(cls, consumptions):
        pool = Pool()
        Invoice = pool.get('account.invoice')

        lines = {}
        for consumption in consumptions:
            line = consumption.get_invoice_line()
            if line:
                lines[consumption.id] = line

        if not lines:
            return []
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
        return invoices

    @classmethod
    def delete(cls, consumptions):
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        lines = InvoiceLine.search([
                ('origin', 'in', [str(c) for c in consumptions])
                ], limit=1)
        if lines:
            line, = lines
            cls.raise_user_error('delete_invoiced_consumption',
                line.origin.rec_name)
        super(ContractConsumption, cls).delete(consumptions)


class CreateConsumptionsStart(ModelView):
    'Create Consumptions Start'
    __name__ = 'contract.create_consumptions.start'
    date = fields.Date('Date', required=True)

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
    create_consumptions = StateAction(
            'contract.act_contract_consumption')

    def do_create_consumptions(self, action):
        pool = Pool()
        Contract = pool.get('contract')
        contracts = Contract.search([
                ('state', '=', 'validated'),
                ('freq', '!=', None),
                ])
        consumptions = Contract.consume(contracts, self.start.date)
        data = {'res_id': [c.id for c in consumptions]}
        if len(consumptions) == 1:
            action['views'].reverse()
        return action, data
