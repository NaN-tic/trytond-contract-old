# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.model import fields
from trytond.pool import PoolMeta

__all__ = ['Party']


class Party:
    __name__ = 'party.party'
    __metaclass__ = PoolMeta

    contract_grouping_method = fields.Property(fields.Selection([
                (None, 'None'),
                ('contract', 'Group contracts'),
                ],
            'Contract Grouping Method'))

    @staticmethod
    def default_contract_grouping_method():
        return None
