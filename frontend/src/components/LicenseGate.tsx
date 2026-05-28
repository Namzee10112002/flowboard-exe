import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  activateLicense,
  getLicenseStatus,
  type LicenseStatus,
} from "../api/client";
import { t, type I18nKey } from "../i18n";

interface LicenseGateProps {
  children: ReactNode;
  onUnlocked: () => void;
}

const MESSAGE_KEY: Record<string, I18nKey> = {
  missing_license_key: "licenseMissingKey",
  license_key_not_found: "licenseKeyNotFound",
  license_hwid_not_bound: "licenseHwidNotBound",
  license_hwid_mismatch: "licenseHwidMismatch",
  license_inactive: "licenseInactive",
  license_expired: "licenseExpired",
  license_sheet_unavailable: "licenseSheetUnavailable",
};

function messageFor(status: LicenseStatus | null, fallback: I18nKey): string {
  const key = status ? MESSAGE_KEY[status.message] : undefined;
  return t(key ?? fallback);
}

export function LicenseGate({ children, onUnlocked }: LicenseGateProps) {
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [licenseKey, setLicenseKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    async function check() {
      setLoading(true);
      setError(null);
      try {
        const next = await getLicenseStatus();
        if (!alive) return;
        setStatus(next);
        if (!next.required || next.licensed) onUnlocked();
      } catch {
        if (!alive) return;
        setError(t("licenseStatusFailed"));
      } finally {
        if (alive) setLoading(false);
      }
    }
    void check();
    return () => {
      alive = false;
    };
  }, [onUnlocked]);

  if (!loading && status && (!status.required || status.licensed)) {
    return <>{children}</>;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!licenseKey.trim()) {
      setError(t("licenseMissingKey"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const next = await activateLicense(licenseKey);
      setStatus(next);
      if (next.licensed) {
        onUnlocked();
        return;
      }
      setError(messageFor(next, "licenseActivateFailed"));
    } catch {
      setError(t("licenseSheetUnavailable"));
    } finally {
      setSubmitting(false);
    }
  }

  async function copyHwid() {
    if (!status?.hwid) return;
    try {
      await navigator.clipboard.writeText(status.hwid);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setError(t("licenseCopyFailed"));
    }
  }

  return (
    <div className="license-screen">
      <main className="license-panel" aria-busy={loading || submitting}>
        <div className="license-panel__brand">Flowboard</div>
        <h1 className="license-panel__title">{t("licenseTitle")}</h1>
        <p className="license-panel__subtitle">{t("licenseSubtitle")}</p>

        <div className="license-hwid">
          <span className="license-hwid__label">{t("licenseHwidLabel")}</span>
          <code className="license-hwid__value">
            {status?.hwid ?? t("licenseChecking")}
          </code>
          <button
            className="license-hwid__copy"
            type="button"
            onClick={copyHwid}
            disabled={!status?.hwid}
          >
            {copied ? t("licenseCopied") : t("licenseCopy")}
          </button>
        </div>

        <form className="license-form" onSubmit={submit}>
          <label className="license-form__label" htmlFor="license-key">
            {t("licenseKeyLabel")}
          </label>
          <input
            id="license-key"
            className="license-form__input"
            value={licenseKey}
            onChange={(event) => setLicenseKey(event.target.value)}
            placeholder={t("licenseKeyPlaceholder")}
            autoComplete="off"
            disabled={loading || submitting}
          />
          <button
            className="license-form__button"
            type="submit"
            disabled={loading || submitting}
          >
            {submitting ? t("licenseActivating") : t("licenseActivate")}
          </button>
        </form>

        {error && <div className="license-error">{error}</div>}
        {loading && <div className="license-muted">{t("licenseChecking")}</div>}
      </main>
    </div>
  );
}
