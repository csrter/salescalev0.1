/**
 * Salescale brand logo — an inline SVG growth-chart mark plus the two-tone
 * "Sale·scale" wordmark. Inline (not an <img>) so it stays crisp at any size
 * and adapts to its surface: the bars inherit the surrounding text color
 * (white on the dark sidebar / auth screens) while the trend line + nodes use
 * the fixed brand blue (--brand-blue).
 *
 * White-labeling: when the current host resolves to a tenant's custom
 * branding (theme.ts), the tenant's product name — and logo image, if set —
 * replace the Salescale identity. logo_url goes through safeBrandUrl(), so
 * only http(s) URLs ever reach the <img src>.
 */

import { safeBrandUrl, useBranding } from "./theme";

export function BrandMark({ size = 26 }: { size?: number }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 40 36"
      fill="none"
      role="img"
      aria-label="Salescale"
    >
      {/* Ascending bars — inherit currentColor from the brand container. */}
      <rect x="3" y="24" width="7" height="10" rx="2" fill="currentColor" opacity="0.92" />
      <rect x="13" y="17" width="7" height="17" rx="2" fill="currentColor" opacity="0.92" />
      <rect x="23" y="9" width="7" height="25" rx="2" fill="currentColor" opacity="0.92" />
      {/* Trend line + nodes in brand blue. */}
      <path
        d="M5 26 L17 17 L30 6"
        stroke="var(--brand-blue)"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="30" cy="6" r="4" fill="var(--brand-blue)" />
      <circle cx="5" cy="26" r="2.6" fill="var(--brand-blue)" />
    </svg>
  );
}

export function Logo({ auth = false }: { auth?: boolean }) {
  const branding = useBranding();
  const cls = auth ? "brand auth-brand" : "brand";

  if (branding.is_custom) {
    const logo = safeBrandUrl(branding.logo_url);
    return (
      <div className={cls}>
        {logo ? (
          <img
            className="brand-logo"
            src={logo}
            alt={branding.product_name}
            height={auth ? 30 : 26}
          />
        ) : (
          <BrandMark size={auth ? 30 : 26} />
        )}
        <span className="brand-name">{branding.product_name}</span>
      </div>
    );
  }

  return (
    <div className={cls}>
      <BrandMark size={auth ? 30 : 26} />
      <span className="brand-name">
        Sale<span className="brand-name-accent">scale</span>
      </span>
    </div>
  );
}
