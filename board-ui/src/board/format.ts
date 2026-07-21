// Small display formatters shared across board components.

const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

const USD_CENTS = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
});

/** Whole-dollar money (cash, targets). */
export function money(n: number): string {
  return USD.format(n);
}

/** Cent-precision money (AI cost). */
export function moneyCents(n: number): string {
  return USD_CENTS.format(n);
}

/** A target amount that may be null/0. */
export function target(n: number | null): string {
  return n && n > 0 ? money(n) : '—';
}

/** Percent label for a [0,1] progress value, or em-dash. */
export function percent(progress: number | null): string {
  return progress === null ? '—' : `${Math.round(progress * 100)}%`;
}
