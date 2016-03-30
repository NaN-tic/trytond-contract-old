=====================================
Monthly Contract, Full Month Scenario
=====================================

.. Define contract with monthly periodicity
.. Start date = Start Period Date = Invoce Date.
.. Create Consumptions.
..      Check consumptions dates.
.. Create Invoice.
..      Check Invoice Lines Amounts
..      Check Invoice Date.

Imports::
    >>> import datetime
    >>> from dateutil.relativedelta import relativedelta
    >>> from decimal import Decimal
    >>> from operator import attrgetter
    >>> from proteus import config, Model, Wizard
    >>> from trytond.modules.company.tests.tools import create_company, \
    ...     get_company
    >>> from trytond.modules.account.tests.tools import create_fiscalyear, \
    ...     create_chart, get_accounts, create_tax, set_tax_code
    >>> from.trytond.modules.account_invoice.tests.tools import \
    ...     set_fiscalyear_invoice_sequences
    >>> today = datetime.datetime.combine(datetime.date.today(),
    ...     datetime.datetime.min.time())
    >>> tomorrow = datetime.date.today() + relativedelta(days=1)

Create database::

    >>> config = config.set_trytond()
    >>> config.pool.test = True

Install contract::

    >>> Module = Model.get('ir.module')
    >>> contract_module, = Module.find([('name', '=', 'contract')])
    >>> Module.install([contract_module.id], config.context)
    >>> Wizard('ir.module.install_upgrade').execute('upgrade')

Create company::

    >>> _ = create_company()
    >>> company = get_company()

Create fiscal year::

    >>> fiscalyear = set_fiscalyear_invoice_sequences(
    ...     create_fiscalyear(company))
    >>> fiscalyear.click('create_period')
    >>> period = fiscalyear.periods[0]

Create chart of accounts::

    >>> _ = create_chart(company)
    >>> accounts = get_accounts(company)
    >>> receivable = accounts['receivable']
    >>> revenue = accounts['revenue']
    >>> expense = accounts['expense']
    >>> account_tax = accounts['tax']

Create tax::

    >>> tax = set_tax_code(create_tax(Decimal('.10')))
    >>> tax.save()
    >>> invoice_base_code = tax.invoice_base_code
    >>> invoice_tax_code = tax.invoice_tax_code
    >>> credit_note_base_code = tax.credit_note_base_code
    >>> credit_note_tax_code = tax.credit_note_tax_code

Create party::

    >>> Party = Model.get('party.party')
    >>> party = Party(name='Party')
    >>> party.save()

Configure unit to accept decimals::

    >>> ProductUom = Model.get('product.uom')
    >>> unit, = ProductUom.find([('name', '=', 'Unit')])
    >>> unit.rounding =  0.01
    >>> unit.digits = 2
    >>> unit.save()

Create product::

    >>> ProductTemplate = Model.get('product.template')
    >>> Product = Model.get('product.product')
    >>> product = Product()
    >>> template = ProductTemplate()
    >>> template.name = 'product'
    >>> template.default_uom = unit
    >>> template.type = 'service'
    >>> template.list_price = Decimal('40')
    >>> template.cost_price = Decimal('25')
    >>> template.account_expense = expense
    >>> template.account_revenue = revenue
    >>> template.customer_taxes.append(tax)
    >>> template.save()
    >>> product.template = template
    >>> product.save()

Create payment term::

    >>> PaymentTerm = Model.get('account.invoice.payment_term')
    >>> payment_term = PaymentTerm(name='Term')
    >>> line = payment_term.lines.new(type='percent', percentage=Decimal(50))
    >>> delta = line.relativedeltas.new(days=20)
    >>> line = payment_term.lines.new(type='remainder')
    >>> delta = line.relativedeltas.new(days=40)
    >>> payment_term.save()
    >>> party.customer_payment_term = payment_term
    >>> party.save()

Create monthly service::

    >>> Service = Model.get('contract.service')
    >>> service = Service()
    >>> service.name = 'Service'
    >>> service.product = product
    >>> service.freq = None
    >>> service.save()


Create a contract::

    >>> Contract = Model.get('contract')
    >>> contract = Contract()
    >>> contract.party = party
    >>> contract.start_period_date = datetime.date(2015, 01, 01)
    >>> contract.freq = 'monthly'
    >>> contract.interval = 1
    >>> contract.first_invoice_date = datetime.date(2015, 02, 10)
    >>> line = contract.lines.new()
    >>> line.start_date = datetime.date(2015, 01, 10)
    >>> line.service = service
    >>> line.unit_price
    Decimal('40')
    >>> line.end_date = datetime.date(2015, 01, 31)
    >>> line2 = contract.lines.new()
    >>> line2.service = service
    >>> line2.unit_price = Decimal('100')
    >>> line2.unit_price
    Decimal('100')
    >>> line2.start_date = datetime.date(2015, 02, 01)
    >>> contract.click('validate_contract')
    >>> contract.state
    u'validated'
    >>> contract.save()
    >>> contract.reload()

Generate consumed lines::

    >>> create_consumptions = Wizard('contract.create_consumptions')
    >>> create_consumptions.form.date = datetime.date(2015, 03, 01)
    >>> create_consumptions.execute('create_consumptions')
    >>> Consumption = Model.get('contract.consumption')
    >>> consumption, consumption2 = Consumption.find([])
    >>> consumption.start_date == datetime.date(2015, 01, 10)
    True
    >>> consumption.end_date == datetime.date(2015, 01, 31)
    True
    >>> consumption.invoice_date == datetime.date(2015, 02, 10)
    True
    >>> consumption.init_period_date == datetime.date(2015, 01, 01)
    True
    >>> consumption.end_period_date == datetime.date(2015, 01, 31)
    True
    >>> consumption2.start_date == datetime.date(2015, 02, 01)
    True
    >>> consumption2.end_date == datetime.date(2015, 02, 28)
    True
    >>> consumption2.invoice_date == datetime.date(2015, 02, 10)
    True
    >>> consumption2.init_period_date == datetime.date(2015, 02, 1)
    True
    >>> consumption2.end_period_date == datetime.date(2015, 02, 28)
    True


Invoice first consumed line::

    >>> invoices = consumption.click('invoice')
    >>> invoice = consumption.invoice_lines[0].invoice
    >>> invoice.type
    u'out'
    >>> invoice.party == party
    True
    >>> invoice.untaxed_amount
    Decimal('28.00')
    >>> invoice.tax_amount
    Decimal('2.80')
    >>> invoice.total_amount
    Decimal('30.80')
    >>> consumption.invoice_lines[0].product == product
    True
    >>> consumption.invoice_date == invoice.invoice_date
    True
    >>> invoice_line, = invoice.lines
    >>> invoice_line.quantity
    0.7

Invoice second consumed line::

    >>> invoices = consumption2.click('invoice')
    >>> invoice = consumption2.invoice_lines[0].invoice
    >>> invoice.type
    u'out'
    >>> invoice.party == party
    True
    >>> invoice.untaxed_amount
    Decimal('100.00')
    >>> invoice.tax_amount
    Decimal('10.00')
    >>> invoice.total_amount
    Decimal('110.00')
    >>> consumption2.invoice_lines[0].product == product
    True
    >>> consumption2.invoice_date == invoice.invoice_date
    True
    >>> invoice_line, = invoice.lines
    >>> invoice_line.quantity
    1.0

