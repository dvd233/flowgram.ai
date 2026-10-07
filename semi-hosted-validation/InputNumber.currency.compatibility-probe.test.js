import fs from 'fs';
import InputNumberFoundation from '@douyinfe/semi-foundation/inputNumber/foundation';

// Diagnostic-only harness input. This is not a proposed contribution test and
// deliberately records outcomes without asserting a mixed-option API contract.
describe('InputNumber currency mixed-option observation', () => {
    it('records actual results without prescribing mixed-option precedence', () => {
        const combinations = [
            { precision: 2, maximumFractionDigits: 0 },
            { precision: 0, minimumFractionDigits: 1 },
            { precision: 2, minimumFractionDigits: 0, maximumFractionDigits: 0 },
            { precision: 2, maximumFractionDigits: 1 },
            { minimumFractionDigits: 3, maximumFractionDigits: 1 },
            { maximumFractionDigits: 0 },
        ];
        const observations = combinations.map(options => {
            const props = {
                currency: 'USD',
                currencyDisplay: 'symbol',
                localeCode: 'en-US',
                showCurrencySymbol: true,
                ...options,
            };
            const foundation = new InputNumberFoundation({
                getProps: () => props,
                getProp: key => props[key],
            });

            try {
                return { options, outcome: { kind: 'value', value: foundation.formatCurrency(12) } };
            } catch (error) {
                return { options, outcome: { kind: 'error', name: error.name, message: error.message } };
            }
        });

        fs.writeFileSync(process.env.SEMI_CURRENCY_OBSERVATIONS, JSON.stringify({
            diagnosticOnly: true,
            noPublicContractAsserted: true,
            observations,
        }, null, 2));
    });
});
