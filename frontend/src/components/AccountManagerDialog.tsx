import { useEffect, useMemo, useState } from "react";
import {
  captureFlowAccountCredential,
  createFlowAccount,
  disableFlowAccount,
  hardDeleteFlowAccount,
  listFlowAccounts,
  openFlowAccountProfile,
  patchFlowAccount,
  resetFlowAccountCooldown,
  testFlowAccount,
  type FlowAccountDTO,
} from "../api/client";
import { t } from "../i18n";

export function AccountManagerDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [accounts, setAccounts] = useState<FlowAccountDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [weight, setWeight] = useState(100);
  const [credentialDraft, setCredentialDraft] = useState<Record<number, string>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [confirmHardDeleteId, setConfirmHardDeleteId] = useState<number | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const rows = await listFlowAccounts();
      setAccounts(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("accountManagerLoadFailed"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    void refresh();
  }, [open]);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 4000);
    return () => clearTimeout(t);
  }, [notice]);

  useEffect(() => {
    if (confirmHardDeleteId === null) return;
    const t = setTimeout(() => setConfirmHardDeleteId(null), 3000);
    return () => clearTimeout(t);
  }, [confirmHardDeleteId]);

  const sortedAccounts = useMemo(
    () => [...accounts].sort((a, b) => b.priority_weight - a.priority_weight),
    [accounts],
  );

  if (!open) return null;

  return (
    <div className="settings-panel-backdrop" role="presentation" onClick={(e) => {
      if (e.target === e.currentTarget) onClose();
    }}>
      <div className="settings-panel" role="dialog" aria-modal="true" aria-label={t("accountManagerTitle")}>
        <div className="settings-panel__header">
          <span className="settings-panel__title">Account Manager</span>
          <button type="button" className="settings-panel__close" onClick={onClose}>×</button>
        </div>

        <div className="settings-panel__section">
          <div className="settings-panel__label">Create account</div>
          <div className="settings-panel__about-row">
            <input className="settings-panel__input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t("accountManagerLabelPlaceholder")} />
            <input className="settings-panel__input settings-panel__input--small" type="number" min={1} max={1000} value={weight} onChange={(e) => setWeight(Math.max(1, Math.min(1000, Number.parseInt(e.target.value || "100", 10))))} />
            <button type="button" className="settings-panel__secondary-btn" disabled={!label.trim()} onClick={async () => {
              setError(null);
              setNotice(null);
              await createFlowAccount({ label: label.trim(), priority_weight: weight, provider: "flow" });
              setLabel("");
              await refresh();
              setNotice(t("accountManagerCreated"));
            }}>{t("accountManagerAdd")}</button>
          </div>
        </div>

        <div className="settings-panel__section">
          <div className="settings-panel__label">Accounts</div>
          {loading ? <div className="settings-panel__hint">Loading…</div> : null}
          {notice ? <div className="settings-panel__hint">{notice}</div> : null}
          {error ? <div className="settings-panel__hint">{error}</div> : null}
          {sortedAccounts.map((account) => (
            <div key={account.id} className="account-manager-card">
              <div className="account-manager-card__head">
                <div className="account-manager-card__title">{account.label}</div>
                <div className="account-manager-card__badges">
                  <span className="account-manager-chip">{account.status}</span>
                  <span className="account-manager-chip">w={account.priority_weight}</span>
                  <span className={`account-manager-chip ${account.credential_configured ? "account-manager-chip--ok" : "account-manager-chip--warn"}`}>
                    {account.credential_configured ? t("accountManagerCredentialReady") : t("accountManagerCredentialMissing")}
                  </span>
                </div>
              </div>
              <div className="account-manager-card__meta">email: {account.email ?? "—"}</div>
              <div className="account-manager-card__meta">tier: {account.paygate_tier ?? t("accountManagerUnknown")} · credits: {account.credits ?? "—"}</div>
              <div className="account-manager-card__meta">cooldown: {account.cooldown_until ? new Date(account.cooldown_until).toLocaleString() : t("accountManagerNone")}</div>
              <div className="account-manager-card__meta">profile: {account.chrome_user_data_dir ?? t("accountManagerNone")}</div>
              {account.last_error ? <div className="account-manager-card__meta">last_error: {account.last_error}</div> : null}
              <div className="account-manager-card__meta">updated: {new Date(account.updated_at).toLocaleString()}</div>

              <div className="account-manager-card__credential-row">
                <input
                  className="settings-panel__input"
                  placeholder={t("accountManagerPasteCredential")}
                  value={credentialDraft[account.id] ?? ""}
                  onChange={(e) => setCredentialDraft((prev) => ({ ...prev, [account.id]: e.target.value }))}
                />
                <button
                  type="button"
                  className="settings-panel__secondary-btn"
                  disabled={busyId === account.id || !(credentialDraft[account.id] ?? "").trim()}
                  onClick={async () => {
                    setBusyId(account.id);
                    setError(null);
                    setNotice(null);
                    try {
                      await patchFlowAccount(account.id, { credential: credentialDraft[account.id] ?? "" });
                      await refresh();
                      setNotice(t("accountManagerSavedCredential", { label: account.label }));
                    } catch (err) {
                      setError(err instanceof Error ? err.message : t("accountManagerSaveCredentialFailed"));
                    } finally {
                      setBusyId(null);
                    }
                  }}
                >
                  Save credential
                </button>
                <button
                  type="button"
                  className="settings-panel__secondary-btn"
                  disabled={busyId === account.id}
                  onClick={async () => {
                    setBusyId(account.id);
                    setError(null);
                    setNotice(null);
                    try {
                      await captureFlowAccountCredential(account.id);
                      await refresh();
                      setNotice(t("accountManagerAutoCaptured", { label: account.label }));
                    } catch (err) {
                      const message = err instanceof Error ? err.message : t("accountManagerAutoCaptureFailed");
                      if (message.includes("extension_disconnected")) {
                        setError(t("errorExtensionDisconnected"));
                      } else if (message.includes("extension_token_not_available")) {
                        setError(t("accountManagerAutoCaptureHint"));
                      } else if (message.includes("browser_token_not_available")) {
                        setError(t("accountManagerAutoCaptureHint"));
                      } else {
                        setError(message);
                      }
                    } finally {
                      setBusyId(null);
                    }
                  }}
                >
                  Auto-capture
                </button>
              </div>

              <div className="account-manager-card__actions">
                <button type="button" className="account-manager-btn" disabled={busyId === account.id} onClick={async () => {
                  setBusyId(account.id);
                  setError(null);
                  setNotice(null);
                  try {
                    const res = await openFlowAccountProfile(account.id);
                    await refresh();
                    setNotice(t("accountManagerProfileOpened", { label: account.label, path: res.launch.profile_dir }));
                  } catch (err) {
                    setError(err instanceof Error ? err.message : t("accountManagerOpenProfileFailed"));
                  } finally {
                    setBusyId(null);
                  }
                }}>{t("accountManagerOpenProfile")}</button>
                <button type="button" className="account-manager-btn" disabled={busyId === account.id} onClick={async () => {
                  setBusyId(account.id);
                  setError(null);
                  setNotice(null);
                  try {
                    await patchFlowAccount(account.id, { status: account.status === "active" ? "paused" : "active" });
                    await refresh();
                    setNotice(`${account.status === "active" ? "Paused" : "Activated"} ${account.label}.`);
                  } finally {
                    setBusyId(null);
                  }
                }}>{account.status === "active" ? t("accountManagerPause") : t("accountManagerActivate")}</button>
                <button type="button" className="account-manager-btn" disabled={busyId === account.id} onClick={async () => {
                  setBusyId(account.id);
                  setError(null);
                  setNotice(null);
                  try {
                    const res = await testFlowAccount(account.id);
                    await refresh();
                    setNotice(t("accountManagerTestOk", { message: res.message }));
                  } catch (err) {
                    setError(err instanceof Error ? err.message : t("accountManagerTestFailed"));
                  } finally {
                    setBusyId(null);
                  }
                }}>{t("accountManagerTest")}</button>
                <button type="button" className="account-manager-btn" disabled={busyId === account.id} onClick={async () => {
                  setBusyId(account.id);
                  setError(null);
                  setNotice(null);
                  try {
                    await resetFlowAccountCooldown(account.id);
                    await refresh();
                    setNotice(t("accountManagerResetDone", { label: account.label }));
                  } finally {
                    setBusyId(null);
                  }
                }}>{t("accountManagerResetCooldown")}</button>
                <button type="button" className="settings-panel__secondary-btn" disabled={busyId === account.id || account.status === "disabled"} onClick={async () => {
                  setBusyId(account.id);
                  setError(null);
                  setNotice(null);
                  try {
                    await disableFlowAccount(account.id);
                    await refresh();
                    setNotice(t("accountManagerDisabled", { label: account.label }));
                  } finally {
                    setBusyId(null);
                  }
                }}>{t("accountManagerDisable")}</button>
                <button type="button" className="account-manager-btn account-manager-btn--danger" disabled={busyId === account.id} onClick={async () => {
                  if (confirmHardDeleteId !== account.id) {
                    setConfirmHardDeleteId(account.id);
                    return;
                  }
                  setBusyId(account.id);
                  setError(null);
                  setNotice(null);
                  try {
                    await hardDeleteFlowAccount(account.id);
                    await refresh();
                    setConfirmHardDeleteId(null);
                    setNotice(t("accountManagerDeleted", { label: account.label }));
                  } catch (err) {
                    setError(err instanceof Error ? err.message : t("accountManagerDeleteFailed"));
                  } finally {
                    setBusyId(null);
                  }
                }}>{confirmHardDeleteId === account.id ? t("accountManagerConfirmDelete") : t("accountManagerDelete")}</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
