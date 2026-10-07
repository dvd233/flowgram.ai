import React from 'react';
import { mount } from 'enzyme';
import InputNumber from '../index';

describe('InputNumber currency zero precision integration', () => {
    it('formats an uncontrolled initial value with zero precision', () => {
        const wrapper = mount(<InputNumber localeCode="en-US" currency="USD" defaultValue={12} precision={0} />);

        try {
            expect(wrapper.find('input').instance().value).toBe('$12');
        } finally {
            wrapper.unmount();
        }
    });

    it('formats a changed value with zero precision on blur', () => {
        const wrapper = mount(<InputNumber localeCode="en-US" currency="USD" defaultValue={12} precision={0} />);

        try {
            wrapper.find('input').simulate('focus');
            wrapper.find('input').simulate('change', { target: { value: '13.6' } });
            wrapper.find('input').simulate('blur');
            expect(wrapper.find('input').instance().value).toBe('$14');
        } finally {
            wrapper.unmount();
        }
    });

    it('formats controlled prop updates with zero precision', () => {
        const wrapper = mount(<InputNumber localeCode="en-US" currency="USD" value={12} precision={0} />);

        try {
            wrapper.setProps({ value: 13.6 });
            expect(wrapper.find('input').instance().value).toBe('$14');
        } finally {
            wrapper.unmount();
        }
    });

    it('honors valid zero fraction bounds with positive precision', () => {
        const wrapper = mount(
            <InputNumber
                localeCode="en-US"
                currency="USD"
                defaultValue={12}
                precision={2}
                minimumFractionDigits={0}
                maximumFractionDigits={0}
            />
        );

        try {
            expect(wrapper.find('input').instance().value).toBe('$12');
        } finally {
            wrapper.unmount();
        }
    });

    it('preserves default currency fraction digits', () => {
        const wrapper = mount(<InputNumber localeCode="en-US" currency="USD" defaultValue={12} />);

        try {
            expect(wrapper.find('input').instance().value).toBe('$12.00');
        } finally {
            wrapper.unmount();
        }
    });

    it('preserves positive currency precision', () => {
        const wrapper = mount(<InputNumber localeCode="en-US" currency="USD" defaultValue={12.3} precision={2} />);

        try {
            expect(wrapper.find('input').instance().value).toBe('$12.30');
        } finally {
            wrapper.unmount();
        }
    });

    it('preserves noncurrency zero precision', () => {
        const wrapper = mount(<InputNumber defaultValue={12.6} precision={0} />);

        try {
            expect(wrapper.find('input').instance().value).toBe('13');
        } finally {
            wrapper.unmount();
        }
    });
});
