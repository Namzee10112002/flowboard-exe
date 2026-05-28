import { useCallback, useEffect, useState } from "react";
import {
  getElevenLabsStatus,
  listElevenLabsVoices,
  setElevenLabsKey,
  type ElevenLabsStatus,
} from "../../api/client";

interface VoiceApiSectionProps {
  force?: boolean;
}

export function VoiceApiSection({ force = false }: VoiceApiSectionProps) {
  const [status, setStatus] = useState<ElevenLabsStatus | null>(null);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await getElevenLabsStatus();
      setStatus(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load voice API status.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  function announceSetupChange() {
    window.dispatchEvent(new CustomEvent("flowboard:setup-config-changed"));
    window.dispatchEvent(new CustomEvent("flowboard:llm-config-changed"));
  }

  async function handleSave() {
    const trimmed = key.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const next = await setElevenLabsKey(trimmed);
      setStatus(next);
      setKey("");
      setMessage("ElevenLabs key saved.");
      announceSetupChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save ElevenLabs key.");
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    if (saving) return;
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const next = await setElevenLabsKey(null);
      setStatus(next);
      setKey("");
      setMessage(next.configured ? "Local key cleared; env key is still active." : "ElevenLabs key cleared.");
      announceSetupChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear ElevenLabs key.");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    if (!status?.configured || testing) return;
    setTesting(true);
    setMessage(null);
    setError(null);
    try {
      const out = await listElevenLabsVoices();
      setMessage(`Voice API connected. Loaded ${out.voices.length} voice(s).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect to ElevenLabs.");
    } finally {
      setTesting(false);
    }
  }

  const configured = status?.configured === true;
  const sourceLabel =
    status?.source === "env"
      ? "environment"
      : status?.source === "local"
        ? "local key"
        : "missing";

  return (
    <section className={`voice-api-section${force && !configured ? " voice-api-section--required" : ""}`}>
      <div className="voice-api-section__head">
        <div>
          <div className="voice-api-section__title">Voice API</div>
          <div className="voice-api-section__subtitle">
            ElevenLabs powers voice replacement for Veo + ElevenLabs scenarios.
          </div>
        </div>
        <span className={`voice-api-section__status${configured ? " voice-api-section__status--ok" : ""}`}>
          {configured ? `Connected (${sourceLabel})` : "Setup required"}
        </span>
      </div>

      <div className="voice-api-section__form">
        <input
          className="voice-api-section__input"
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="Paste ElevenLabs API key"
          autoComplete="off"
        />
        <button
          type="button"
          className="voice-api-section__btn voice-api-section__btn--primary"
          onClick={handleSave}
          disabled={saving || !key.trim()}
        >
          {saving ? "Saving..." : "Save key"}
        </button>
        <button
          type="button"
          className="voice-api-section__btn"
          onClick={handleTest}
          disabled={testing || !configured}
        >
          {testing ? "Testing..." : "Test"}
        </button>
        <button
          type="button"
          className="voice-api-section__btn"
          onClick={handleClear}
          disabled={saving || !configured || status?.source === "env"}
          title={status?.source === "env" ? "Clear ELEVENLABS_API_KEY from the environment to remove it." : undefined}
        >
          Clear
        </button>
      </div>

      <div className="voice-api-section__hint">
        The key stays on this machine in <code>~/.flowboard/secrets.json</code> and is never shown again.
      </div>
      {message && <div className="voice-api-section__message">{message}</div>}
      {error && <div className="voice-api-section__error" role="alert">{error}</div>}
    </section>
  );
}
