=================
Contract Grouping
=================

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

Install contract module::

    >>> Module = Model.get('ir.module')
    >>> contract_module, = Module.find([('name', '=', 'contract')])
    >>> contract_module.click('install')
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
    >>> party.contract_grouping_method = 'contract'
    >>> party.save()
    >>> non_grouping_party = Party(name='Party')
    >>> non_grouping_party.save()

Create product::

    >>> ProductUom = Model.get('product.uom')
    >>> unit, = ProductUom.find([('name', '=', 'Unit')])
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
    >>> non_grouping_party.customer_payment_term = payment_term
    >>> non_grouping_party.save()

Create monthly service::

    >>> Service = Model.get('contract.service')
    >>> service = Service()
    >>> service.name = 'Service'
    >>> service.product = product
    >>> service.save()

Create two contract for grouped party::

    >>> Contract = Model.get('contract')
    >>> contract = Contract()
    >>> contract.party = party
    >>> contract.start_period_date = datetime.date(today.year, 01, 01)
    >>> contract.freq = 'monthly'
    >>> contract.first_invoice_date = datetime.date(today.year, 01, 31)
    >>> line = contract.lines.new()
    >>> line.start_date = datetime.date(today.year, 01, 01)
    >>> line.service = service
    >>> line.unit_price
    Decimal('40')
    >>> contract.click('validate_contract')
    >>> contract.state
    u'validated'
    >>> contract = Contract()
    >>> contract.party = party
    >>> contract.start_period_date = datetime.date(today.year, 01, 01)
    >>> contract.start_date = datetime.date(today.year, 01, 01)
    >>> contract.freq = 'monthly'
    >>> contract.first_invoice_date = datetime.date(today.year, 01, 31)
    >>> line = contract.lines.new()
    >>> line.start_date = datetime.date(today.year, 01, 01)
    >>> line.service = service
    >>> line.unit_price
    Decimal('40')
    >>> contract.click('validate_contract')
    >>> contract.state
    u'validated'


Create two contract for non grouped party::

    >>> contract = Contract()
    >>> contract.party = non_grouping_party
    >>> contract.start_period_date = datetime.date(today.year, 01, 01)
    >>> contract.freq = 'monthly'
    >>> contract.first_invoice_date = datetime.date(today.year, 01, 31)
    >>> line = contract.lines.new()
    >>> line.start_date = datetime.date(today.year, 01, 01)
    >>> line.service = service
    >>> line.unit_price
    Decimal('40')
    >>> contract.click('validate_contract')
    >>> contract.state
    u'validated'
    >>> contract = Contract()
    >>> contract.party = non_grouping_party
    >>> contract.start_period_date = datetime.date(today.year, 01, 01)
    >>> contract.start_date = datetime.date(today.year, 01, 01)
    >>> contract.freq = 'monthly'
    >>> contract.first_invoice_date = datetime.date(today.year, 01, 31)
    >>> line = contract.lines.new()
    >>> line.start_date = datetime.date(today.year, 01, 01)
    >>> line.service = service
    >>> line.unit_price
    Decimal('40')
    >>> contract.click('validate_contract')
    >>> contract.state
    u'validated'

Generate consumed lines::

    >>> create_consumptions = Wizard('contract.create_consumptions')
    >>> create_consumptions.form.date = datetime.date(today.year, 02, 01)
    >>> create_consumptions.execute('create_consumptions')

Generate invoice for consumed lines::

    >>> create_invoice = Wizard('contract.create_invoices')
    >>> create_invoice.form.date = datetime.date(today.year, 02, 01)
    >>> create_invoice.execute('create_invoices')

Only one invoice is generated for grouping party::

    >>> Invoice = Model.get('account.invoice')
    >>> invoice, = Invoice.find([('party', '=', party.id)])
    >>> invoice.untaxed_amount
    Decimal('80.00')
    >>> invoice.tax_amount
    Decimal('8.00')
    >>> invoice.total_amount
    Decimal('88.00')
    >>> len(invoice.lines)
    2

Two invoices are generated for non grouping party::

    >>> Invoice = Model.get('account.invoice')
    >>> first_invoice, second_invoice = Invoice.find([
    ...     ('party', '=', non_grouping_party.id)])
    >>> first_invoice.untaxed_amount
    Decimal('40.00')
    >>> first_invoice.total_amount
    Decimal('44.00')
    >>> second_invoice.untaxed_amount
    Decimal('40.00')
    >>> second_invoice.total_amount
    Decimal('44.00')
