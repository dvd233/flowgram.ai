import InputNumberFoundation from '@douyinfe/semi-foundation/inputNumber/foundation';

describe('InputNumberFoundation currency fraction digits', () => {
    const createFoundation = (options = {}) => {
        const props = {
            currency: 'USD',
            currencyDisplay: 'symbol',
            localeCode: 'en-US',
            showCurrencySymbol: true,
            ...options,
        };

        return new InputNumberFoundation({
            getProps: () => props,
            getProp: key => props[key],
        });
    };

    it('keeps the default USD fraction digits when no precision options are supplied', () => {
        const foundation = createFoundation();

        expect(foundation.formatCurrency(12)).toBe('$12.00');
    });

    it('keeps a positive precision', () => {
        const foundation = createFoundation({ precision: 2 });

        expect(foundation.formatCurrency(12.3)).toBe('$12.30');
    });

    it('keeps positive minimum and maximum fraction digit options', () => {
        const foundation = createFoundation({
            minimumFractionDigits: 2,
            maximumFractionDigits: 4,
        });

        expect(foundation.formatCurrency(12.3456)).toBe('$12.3456');
    });

    it('respects an explicit minimumFractionDigits of zero', () => {
        const foundation = createFoundation({ minimumFractionDigits: 0 });

        expect(foundation.formatCurrency(12)).toBe('$12');
    });

    it('respects an explicit maximumFractionDigits of zero', () => {
        const foundation = createFoundation({ maximumFractionDigits: 0 });

        expect(foundation.formatCurrency(12)).toBe('$12');
    });

    it('respects an explicit precision of zero', () => {
        const foundation = createFoundation({ precision: 0 });

        expect(foundation.formatCurrency(12)).toBe('$12');
    });

    it('respects explicit zero minimum and maximum fraction digits together', () => {
        const foundation = createFoundation({
            minimumFractionDigits: 0,
            maximumFractionDigits: 0,
        });

        expect(foundation.formatCurrency(12)).toBe('$12');
    });
});

