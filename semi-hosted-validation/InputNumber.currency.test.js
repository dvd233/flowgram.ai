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

    it('uses currency defaults for explicitly undefined options', () => {
        const foundation = createFoundation({ minimumFractionDigits: undefined, maximumFractionDigits: undefined, precision: undefined });

        expect(foundation.formatCurrency(12)).toBe('$12.00');
    });

    it('uses currency defaults for null precision', () => {
        const foundation = createFoundation({ precision: null });

        expect(foundation.formatCurrency(12)).toBe('$12.00');
    });

    it('uses currency defaults when all precision options are null', () => {
        const foundation = createFoundation({ minimumFractionDigits: null, maximumFractionDigits: null, precision: null });

        expect(foundation.formatCurrency(12)).toBe('$12.00');
    });

    it('falls back from null fraction bounds to positive precision', () => {
        const foundation = createFoundation({ minimumFractionDigits: null, maximumFractionDigits: null, precision: 2 });

        expect(foundation.formatCurrency(12.3)).toBe('$12.30');
    });

    it('falls back from null fraction bounds to zero precision', () => {
        const foundation = createFoundation({ minimumFractionDigits: null, maximumFractionDigits: null, precision: 0 });

        expect(foundation.formatCurrency(12.6)).toBe('$13');
    });

    it('lets an explicit zero minimum override positive precision', () => {
        const foundation = createFoundation({ minimumFractionDigits: 0, precision: 2 });

        expect(foundation.formatCurrency(12)).toBe('$12');
    });

    it('lets valid explicit zero bounds override positive precision', () => {
        const foundation = createFoundation({ minimumFractionDigits: 0, maximumFractionDigits: 0, precision: 2 });

        expect(foundation.formatCurrency(12.6)).toBe('$13');
    });

    it('keeps positive bounds ahead of precision for each endpoint', () => {
        const foundation = createFoundation({ minimumFractionDigits: 1, maximumFractionDigits: 3, precision: 2 });

        expect(foundation.formatCurrency(12.3456)).toBe('$12.346');
    });

    it('keeps the default JPY fraction digits', () => {
        const foundation = createFoundation({ currency: 'JPY' });

        expect(foundation.formatCurrency(12.6)).toBe('¥13');
    });

    it('retains Intl validation for invalid explicit positive bounds', () => {
        const foundation = createFoundation({ minimumFractionDigits: 3, maximumFractionDigits: 1 });

        expect(() => foundation.formatCurrency(12)).toThrow(RangeError);
    });

    it('retains Intl validation for a positive precision fallback conflict', () => {
        const foundation = createFoundation({ precision: 2, maximumFractionDigits: 1 });

        expect(() => foundation.formatCurrency(12)).toThrow(RangeError);
    });

});
