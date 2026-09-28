import { afterAll, describe, expect, it } from 'vitest';
import i18n from '@/i18n';

describe('document language', () => {
  afterAll(async () => {
    await i18n.changeLanguage('zh');
  });

  it('tracks the active locale so locale-specific typography applies', async () => {
    await i18n.changeLanguage('vi');
    expect(document.documentElement.lang).toBe('vi');

    await i18n.changeLanguage('en');
    expect(document.documentElement.lang).toBe('en');
  });
});
