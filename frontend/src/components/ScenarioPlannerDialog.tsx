import { useEffect, useMemo, useRef, useState } from "react";
import {
  analyzeBatchImageScenarios,
  analyzeBatchVideoScenarios,
  createRequest,
  createScenarioBatchRecords,
  deleteScenario,
  exportScenario,
  exportSelectedScenarios,
  getScenario,
  getRequest,
  listElevenLabsVoices,
  listScenarios,
  mediaUrl,
  patchScenario,
  patchScenarioScene,
  planScenario,
  scanScenarioBatchFolder,
  type BatchScanInputType,
  type ElevenLabsVoice,
  type ScenarioDTO,
  type ScenarioGenerationStage,
  type ScenarioPlanInput,
  type ScenarioPlanResponse,
  type ScenarioSceneDTO,
  type VideoAudioMode,
} from "../api/client";
import { useBoardStore, type FlowNode } from "../store/board";
import { useGenerationStore } from "../store/generation";
import { useSettingsStore } from "../store/settings";
import { t } from "../i18n";

interface ScenarioPlannerDialogProps {
  open: boolean;
  onClose: () => void;
}

const CONTENT_STYLES = [
  "Premium fashion ad",
  "TikTok UGC",
  "Luxury editorial",
  "Product storytelling",
  "Cinematic brand film",
  "Beauty commercial",
  "Streetwear launch",
  "Lifestyle vlog",
  "Product unboxing",
  "Founder story",
  "Customer testimonial",
  "Educational explainer",
  "Before after transformation",
  "Music video",
  "Comedy skit",
  "ASMR product showcase",
  "Food commercial",
  "Fitness campaign",
  "Travel reel",
  "Real estate tour",
  "Documentary mini film",
  "Tech product demo",
  "Game trailer",
  "Anime promo",
  "Event recap",
] as const;

const VIDEO_AUDIO_MODES: Array<
  readonly [VideoAudioMode, string, string]
> = [
  ["silent", t("scenarioAudioSilent"), t("scenarioAudioSilentHint")],
  ["veo_dialogue", t("scenarioAudioVeo"), t("scenarioAudioVeoHint")],
  ["veo_dialogue_elevenlabs_replace", t("scenarioAudioVeoEleven"), t("scenarioAudioVeoElevenHint")],
];

const SCENARIO_STAGE_FILTERS: Array<
  readonly ["all" | ScenarioGenerationStage, string]
> = [
  ["all", t("scenarioFilterAll")],
  ["needs_background", t("scenarioFilterNeedsBackground")],
  ["needs_scene_image", t("scenarioFilterNeedsSceneImage")],
  ["needs_video", t("scenarioFilterNeedsVideo")],
  ["video_done", t("scenarioFilterVideoDone")],
  ["error", t("scenarioFilterError")],
];

type ContentStyle = (typeof CONTENT_STYLES)[number];

function asContentStyle(value: string): ContentStyle {
  return (CONTENT_STYLES as readonly string[]).includes(value)
    ? (value as ContentStyle)
    : t("scenarioDefaultStyle") as ContentStyle;
}

function asVideoAudioMode(value: string | null | undefined): VideoAudioMode {
  return VIDEO_AUDIO_MODES.some(([mode]) => mode === value)
    ? (value as VideoAudioMode)
    : "silent";
}

function videoAudioModeLabel(value: string | null | undefined): string {
  return VIDEO_AUDIO_MODES.find(([mode]) => mode === value)?.[1] ?? t("scenarioAudioSilent");
}

function scenarioStageLabel(value: string | null | undefined): string {
  return SCENARIO_STAGE_FILTERS.find(([stage]) => stage === value)?.[1] ?? t("scenarioEmpty");
}

function scenarioStage(scenario: ScenarioDTO): ScenarioGenerationStage {
  return scenario.generation_summary?.stage ?? "empty";
}

function scenarioHasRemainingWork(scenario: ScenarioDTO): boolean {
  const summary = scenario.generation_summary;
  if (!summary) return true;
  return summary.stage !== "video_done";
}

function formatScenarioDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function primaryMediaId(node: FlowNode): string | null {
  if (typeof node.data.mediaId === "string" && node.data.mediaId.length > 0) {
    return node.data.mediaId;
  }
  const variants = node.data.mediaIds ?? [];
  const first = variants.find((m): m is string => typeof m === "string" && m.length > 0);
  return first ?? null;
}

function collectRefCandidates(nodes: FlowNode[], type: FlowNode["data"]["type"]) {
  return nodes
    .filter((node) => node.data.type === type)
    .map((node) => ({ node, mediaId: primaryMediaId(node) }))
    .filter((item): item is { node: FlowNode; mediaId: string } => item.mediaId !== null);
}

function toggleId(ids: string[], id: string): string[] {
  return ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function firstMediaId(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  const found = value.find((item): item is string => (
    typeof item === "string" && item.length > 0
  ));
  return found ?? null;
}

function mediaIdList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => (
    typeof item === "string" && item.length > 0
  ));
}

function uniqueMediaIds(ids: string[]): string[] {
  return Array.from(new Set(ids.filter((id) => id.length > 0)));
}

async function waitForRequestDone(requestId: number, maxAttempts = 240) {
  let networkErrors = 0;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    await sleep(attempt === 0 ? 800 : 1500);
    try {
      const req = await getRequest(requestId);
      networkErrors = 0;
      if (req.status === "done" || req.status === "failed") return req;
    } catch (err) {
      networkErrors += 1;
      if (networkErrors >= 8) throw err;
    }
  }
  throw new Error(t("scenarioGenerationTimedOut"));
}

function RefPicker({
  title,
  hint,
  empty,
  candidates,
  selected,
  onToggle,
}: {
  title: string;
  hint: string;
  empty: string;
  candidates: Array<{ node: FlowNode; mediaId: string }>;
  selected: string[];
  onToggle: (mediaId: string) => void;
}) {
  return (
    <section className="scenario-dialog__ref-section">
      <div className="scenario-dialog__section-head">
        <div>
          <h3 className="scenario-dialog__section-title">{title}</h3>
          <p className="scenario-dialog__section-hint">{hint}</p>
        </div>
        <span className="scenario-dialog__count">{selected.length}</span>
      </div>
      {candidates.length === 0 ? (
        <div className="scenario-dialog__empty">{empty}</div>
      ) : (
        <div className="scenario-ref-grid">
          {candidates.map(({ node, mediaId }) => {
            const checked = selected.includes(mediaId);
            return (
              <button
                key={`${node.id}:${mediaId}`}
                type="button"
                className={`scenario-ref-card${checked ? " scenario-ref-card--selected" : ""}`}
                onClick={() => onToggle(mediaId)}
                aria-pressed={checked}
                title={node.data.title}
              >
                <img
                  className="scenario-ref-card__img"
                  src={mediaUrl(mediaId)}
                  alt={node.data.title}
                />
                <span className="scenario-ref-card__meta">
                  #{node.data.shortId}
                </span>
                <span className="scenario-ref-card__check" aria-hidden="true">
                  {checked ? "✓" : ""}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

type SceneTextKey =
  | "background_description"
  | "background_image_prompt"
  | "composition_prompt"
  | "motion_prompt"
  | "voice_script"
  | "voice_direction";

type GenerationProgress = Record<
  number,
  { status: "queued" | "running" | "done" | "error"; error?: string }
>;

type ScenarioRunProgress = Record<
  number,
  { status: "queued" | "running" | "done" | "error"; label?: string; error?: string }
>;

type PlannerMode = "manual" | "batch";
type BatchInputType = BatchScanInputType;
type BatchFileStatus = "ready" | "queued" | "analyzing" | "transcribing" | "planning" | "saved" | "error";

interface BatchFileItem {
  id: string;
  name: string;
  kind: "image" | "video";
  status: BatchFileStatus;
  progress: number;
  message?: string;
}

interface ScenarioActivity {
  status: "queued" | "running" | "done" | "error";
  label: string;
  done: number;
  total: number;
  error?: string;
}

type ScenarioActivities = Record<number, ScenarioActivity>;

const SCENE_TEXT_FIELDS: Array<readonly [string, SceneTextKey, number]> = [
  ["Background description", "background_description", 2],
  ["Background image prompt", "background_image_prompt", 3],
  ["Composition prompt", "composition_prompt", 4],
  ["Motion prompt", "motion_prompt", 3],
  ["Voice script", "voice_script", 2],
  ["Voice direction", "voice_direction", 2],
];

const EDITABLE_SCENE_KEYS = [
  "title",
  "background_description",
  "background_image_prompt",
  "composition_prompt",
  "motion_prompt",
  "voice_script",
  "voice_direction",
  "duration_seconds",
] as const;

function editableSceneShape(scene: ScenarioSceneDTO) {
  const out: Record<string, string | number> = {};
  for (const key of EDITABLE_SCENE_KEYS) {
    const value = scene[key];
    out[key] = typeof value === "number" ? value : String(value ?? "");
  }
  return out;
}

function mergeScenesById(
  scenes: ScenarioSceneDTO[],
  updates: ScenarioSceneDTO[],
): ScenarioSceneDTO[] {
  const byId = new Map(updates.map((scene) => [scene.id, scene]));
  return scenes
    .map((scene) => byId.get(scene.id) ?? scene)
    .sort((a, b) => a.idx - b.idx);
}

function remainingAutoStepCount(
  scenes: ScenarioSceneDTO[],
  needsVoice: boolean,
): number {
  return scenes.reduce((count, scene) => {
    const hasBackground = Boolean(scene.background_media_id);
    const hasImage = hasBackground && Boolean(scene.image_media_id);
    const hasVideo = hasImage && Boolean(scene.video_media_id);
    return (
      count
      + (hasBackground ? 0 : 1)
      + (hasImage ? 0 : 1)
      + (hasVideo ? 0 : 1)
      + (needsVoice && !scene.voice_media_id ? 1 : 0)
    );
  }, 0);
}

async function runLimited<T>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<void>,
): Promise<void> {
  let nextIndex = 0;
  const workerCount = Math.min(limit, items.length);
  await Promise.all(
    Array.from({ length: workerCount }, async () => {
      while (nextIndex < items.length) {
        const item = items[nextIndex];
        nextIndex += 1;
        await worker(item);
      }
    }),
  );
}

function activityPercent(activity: ScenarioActivity | undefined): number {
  if (!activity) return 0;
  if (activity.status === "done") return 100;
  const total = Math.max(1, activity.total);
  return Math.max(0, Math.min(99, Math.round((activity.done / total) * 100)));
}

function isRunningActivity(activity: ScenarioActivity | undefined): boolean {
  return activity?.status === "queued" || activity?.status === "running";
}

function generationProgressPercent(progress: GenerationProgress[number] | undefined): number {
  if (!progress) return 0;
  if (progress.status === "done") return 100;
  if (progress.status === "error") return 100;
  if (progress.status === "running") return 65;
  return 15;
}

function titleCaseStatus(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function compositionRefIds(scene: ScenarioSceneDTO): string[] {
  return uniqueMediaIds([
    ...(scene.background_media_id ? [scene.background_media_id] : []),
    ...mediaIdList(scene.refs.character_media_ids),
    ...mediaIdList(scene.refs.visual_asset_media_ids),
  ]);
}

function videoPromptForScene(
  scene: ScenarioSceneDTO,
  mode: VideoAudioMode,
): { prompt: string; error: string | null } {
  const base = scene.motion_prompt.trim();
  if (!base) return { prompt: "", error: "missing motion prompt" };
  if (mode === "silent") return { prompt: base, error: null };

  const line = scene.voice_script.trim();
  if (!line) return { prompt: "", error: "missing voice script" };

  const direction = scene.voice_direction.trim();
  const dialoguePrompt = [
    base,
    "",
    `Native dialogue audio: the on-screen character says exactly: ${JSON.stringify(line)}.`,
    direction ? `Voice performance: ${direction}.` : "",
    "Natural synchronized lip movement, no subtitles, no text overlays.",
  ]
    .filter(Boolean)
    .join("\n");

  return { prompt: dialoguePrompt, error: null };
}

function SceneTextarea({
  label,
  value,
  rows,
  onChange,
}: {
  label: string;
  value: string;
  rows?: number;
  onChange: (value: string) => void;
}) {
  return (
    <label className="scenario-scene-field">
      <span>{label}</span>
      <textarea
        className="scenario-scene-field__textarea"
        value={value}
        rows={rows ?? 3}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function ScenePlanEditor({
  scenes,
  backgroundProgress,
  imageProgress,
  videoProgress,
  onSceneChange,
}: {
  scenes: ScenarioSceneDTO[];
  backgroundProgress: GenerationProgress;
  imageProgress: GenerationProgress;
  videoProgress: GenerationProgress;
  onSceneChange: (sceneId: number, patch: Partial<ScenarioSceneDTO>) => void;
}) {
  if (scenes.length === 0) {
    return <div className="scenario-dialog__placeholder">No scenes planned yet.</div>;
  }

  return (
    <div className="scenario-scene-list">
      {scenes.map((scene) => {
        const currentProgress = (
          videoProgress[scene.id]
          ?? imageProgress[scene.id]
          ?? backgroundProgress[scene.id]
        );
        const status = currentProgress?.status ?? scene.status;
        const backgroundError = backgroundProgress[scene.id]?.error;
        const imageError = imageProgress[scene.id]?.error;
        const videoError = videoProgress[scene.id]?.error;
        const displayError = videoError ?? imageError ?? backgroundError ?? scene.error;
        const isSceneActive = currentProgress?.status === "queued" || currentProgress?.status === "running";
        const sceneProgressPercent = generationProgressPercent(currentProgress);
        return (
        <article
          className={`scenario-scene-card${isSceneActive ? " scenario-scene-card--active" : ""}`}
          key={scene.id}
        >
          <div className="scenario-scene-card__head">
            <span className="scenario-scene-card__badge">Scene {scene.idx + 1}</span>
            <span className={`scenario-scene-status scenario-scene-status--${status}`}>
              {status}
            </span>
            <label className="scenario-scene-duration">
              <span>Duration</span>
              <input
                type="number"
                min={1}
                max={60}
                step={0.5}
                value={scene.duration_seconds}
                onChange={(e) => {
                  const next = Number.parseFloat(e.target.value);
                  onSceneChange(scene.id, {
                    duration_seconds: Number.isFinite(next) ? next : 8,
                  });
                }}
              />
            </label>
          </div>
          {currentProgress ? (
            <div className={`scenario-scene-request scenario-scene-request--${currentProgress.status}`}>
              <div className="scenario-scene-request__head">
                <span>{titleCaseStatus(currentProgress.status)}</span>
                <strong>{sceneProgressPercent}%</strong>
              </div>
              <div className="scenario-scene-request__bar">
                <span
                  className="scenario-scene-request__fill"
                  style={{ width: `${sceneProgressPercent}%` }}
                />
              </div>
            </div>
          ) : null}

          <label className="scenario-scene-field">
            <span>Scene title</span>
            <input
              className="scenario-scene-field__input"
              value={scene.title}
              onChange={(e) => onSceneChange(scene.id, { title: e.target.value })}
            />
          </label>

          <div className="scenario-scene-media-grid">
            <div className="scenario-scene-media">
              <span className="scenario-scene-media__label">Background</span>
              {scene.background_media_id ? (
                <img
                  className="scenario-scene-media__img"
                  src={mediaUrl(scene.background_media_id)}
                  alt={`${scene.title} background`}
                />
              ) : (
                <div className={`scenario-scene-media__empty${
                  backgroundProgress[scene.id]?.status === "queued" ||
                  backgroundProgress[scene.id]?.status === "running"
                    ? " scenario-scene-media__empty--active"
                    : ""
                }`}>
                  {backgroundProgress[scene.id]?.status === "queued" ||
                  backgroundProgress[scene.id]?.status === "running"
                    ? "Generating background..."
                    : "No background generated"}
                </div>
              )}
            </div>
            <div className="scenario-scene-media">
              <span className="scenario-scene-media__label">Scene image</span>
              {scene.image_media_id ? (
                <img
                  className="scenario-scene-media__img"
                  src={mediaUrl(scene.image_media_id)}
                  alt={`${scene.title} composition`}
                />
              ) : (
                <div className={`scenario-scene-media__empty${
                  imageProgress[scene.id]?.status === "queued" ||
                  imageProgress[scene.id]?.status === "running"
                    ? " scenario-scene-media__empty--active"
                    : ""
                }`}>
                  {imageProgress[scene.id]?.status === "queued" ||
                  imageProgress[scene.id]?.status === "running"
                    ? "Generating scene image..."
                    : "No scene image generated"}
                </div>
              )}
            </div>
            <div className="scenario-scene-media">
              <span className="scenario-scene-media__label">Video</span>
              {scene.video_media_id ? (
                <video
                  className="scenario-scene-media__img"
                  src={mediaUrl(scene.video_media_id)}
                  controls
                  muted
                  playsInline
                />
              ) : (
                <div className={`scenario-scene-media__empty${
                  videoProgress[scene.id]?.status === "queued" ||
                  videoProgress[scene.id]?.status === "running"
                    ? " scenario-scene-media__empty--active"
                    : ""
                }`}>
                  {videoProgress[scene.id]?.status === "queued" ||
                  videoProgress[scene.id]?.status === "running"
                    ? "Generating video..."
                    : "No video generated"}
                </div>
              )}
            </div>
            {displayError ? (
              <div className="scenario-scene-media__error">
                {displayError}
              </div>
            ) : null}
          </div>

          {scene.voice_media_id ? (
            <div className="scenario-scene-audio">
              <span className="scenario-scene-media__label">Voice</span>
              <audio
                className="scenario-scene-audio__player"
                src={mediaUrl(scene.voice_media_id)}
                controls
              />
            </div>
          ) : null}

          {SCENE_TEXT_FIELDS.map(([label, key, rows]) => (
            <SceneTextarea
              key={key}
              label={label}
              value={scene[key]}
              rows={rows}
              onChange={(value) => (
                onSceneChange(scene.id, { [key]: value } as Partial<ScenarioSceneDTO>)
              )}
            />
          ))}
        </article>
        );
      })}
    </div>
  );
}

export function ScenarioPlannerDialog({ open, onClose }: ScenarioPlannerDialogProps) {
  const nodes = useBoardStore((s) => s.nodes);
  const boardName = useBoardStore((s) => s.boardName);
  const boardId = useBoardStore((s) => s.boardId);
  const ensureProjectId = useGenerationStore((s) => s.ensureProjectId);
  const paygateTier = useGenerationStore((s) => s.paygateTier);
  const imageModel = useSettingsStore((s) => s.imageModel);
  const videoQuality = useSettingsStore((s) => s.videoQuality);

  const [plannerMode, setPlannerMode] = useState<PlannerMode>("manual");
  const [theme, setTheme] = useState("");
  const [extraDescription, setExtraDescription] = useState("");
  const [sceneCount, setSceneCount] = useState(5);
  const [contentStyle, setContentStyle] = useState<ContentStyle>("Premium fashion ad");
  const [videoAudioMode, setVideoAudioMode] = useState<VideoAudioMode>("silent");
  const [batchFolderPath, setBatchFolderPath] = useState("");
  const [batchInputType, setBatchInputType] = useState<BatchInputType>("auto");
  const [batchSceneCount, setBatchSceneCount] = useState(5);
  const [batchVoiceId, setBatchVoiceId] = useState("");
  const [batchFiles, setBatchFiles] = useState<BatchFileItem[]>([]);
  const [backgroundIds, setBackgroundIds] = useState<string[]>([]);
  const [characterIds, setCharacterIds] = useState<string[]>([]);
  const [assetIds, setAssetIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ScenarioPlanInput | null>(null);
  const [result, setResult] = useState<ScenarioPlanResponse | null>(null);
  const [sceneDrafts, setSceneDrafts] = useState<ScenarioSceneDTO[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [generatingBackgrounds, setGeneratingBackgrounds] = useState(false);
  const [backgroundProgress, setBackgroundProgress] = useState<GenerationProgress>({});
  const [generatingImages, setGeneratingImages] = useState(false);
  const [imageProgress, setImageProgress] = useState<GenerationProgress>({});
  const [generatingVideos, setGeneratingVideos] = useState(false);
  const [videoProgress, setVideoProgress] = useState<GenerationProgress>({});
  const [generatingAll, setGeneratingAll] = useState(false);
  const [generateAllDone, setGenerateAllDone] = useState(0);
  const [generateAllTotal, setGenerateAllTotal] = useState(0);
  const [generateAllLabel, setGenerateAllLabel] = useState("");
  const [generatingSelected, setGeneratingSelected] = useState(false);
  const [selectedGenerateDone, setSelectedGenerateDone] = useState(0);
  const [selectedGenerateTotal, setSelectedGenerateTotal] = useState(0);
  const [selectedGenerateLabel, setSelectedGenerateLabel] = useState("");
  const [selectedScenarioProgress, setSelectedScenarioProgress] = (
    useState<ScenarioRunProgress>({})
  );
  const [scenarioActivities, setScenarioActivities] = useState<ScenarioActivities>({});
  const [exportingScenario, setExportingScenario] = useState(false);
  const [exportingSelected, setExportingSelected] = useState(false);
  const [voices, setVoices] = useState<ElevenLabsVoice[]>([]);
  const [loadingVoices, setLoadingVoices] = useState(false);
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [scenarioHistory, setScenarioHistory] = useState<ScenarioDTO[]>([]);
  const [scenarioSearch, setScenarioSearch] = useState("");
  const [scenarioStageFilter, setScenarioStageFilter] = useState<"all" | ScenarioGenerationStage>("all");
  const [selectedScenarioIds, setSelectedScenarioIds] = useState<number[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingScenarioId, setLoadingScenarioId] = useState<number | null>(null);
  const [deletingScenarioId, setDeletingScenarioId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const firstInputRef = useRef<HTMLInputElement>(null);
  const currentScenarioIdRef = useRef<number | null>(null);
  currentScenarioIdRef.current = result?.scenario.id ?? null;

  function isCurrentScenarioId(scenarioId: number): boolean {
    return currentScenarioIdRef.current === scenarioId;
  }

  function setBackgroundProgressForScenario(
    scenarioId: number,
    sceneId: number,
    progress: GenerationProgress[number],
  ) {
    if (!isCurrentScenarioId(scenarioId)) return;
    setBackgroundProgress((prev) => ({ ...prev, [sceneId]: progress }));
  }

  function setImageProgressForScenario(
    scenarioId: number,
    sceneId: number,
    progress: GenerationProgress[number],
  ) {
    if (!isCurrentScenarioId(scenarioId)) return;
    setImageProgress((prev) => ({ ...prev, [sceneId]: progress }));
  }

  function setVideoProgressForScenario(
    scenarioId: number,
    sceneId: number,
    progress: GenerationProgress[number],
  ) {
    if (!isCurrentScenarioId(scenarioId)) return;
    setVideoProgress((prev) => ({ ...prev, [sceneId]: progress }));
  }

  const backgroundCandidates = useMemo(
    () => collectRefCandidates(nodes, "image"),
    [nodes],
  );
  const characterCandidates = useMemo(
    () => collectRefCandidates(nodes, "character"),
    [nodes],
  );
  const assetCandidates = useMemo(
    () => collectRefCandidates(nodes, "visual_asset"),
    [nodes],
  );
  const filteredScenarioHistory = useMemo(() => {
    const needle = scenarioSearch.trim().toLowerCase();
    return scenarioHistory.filter((scenario) => {
      if (
        scenarioStageFilter !== "all"
        && scenarioStage(scenario) !== scenarioStageFilter
      ) {
        return false;
      }
      if (!needle) return true;
      const haystack = [
        scenario.id,
        scenario.theme,
        scenario.status,
        scenario.content_style,
        scenario.scene_count,
        videoAudioModeLabel(scenario.video_audio_mode),
        scenarioStageLabel(scenarioStage(scenario)),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [scenarioHistory, scenarioSearch, scenarioStageFilter]);
  const filteredScenarioIds = useMemo(
    () => filteredScenarioHistory.map((scenario) => scenario.id),
    [filteredScenarioHistory],
  );
  const selectedVisibleCount = filteredScenarioIds.filter((id) => (
    selectedScenarioIds.includes(id)
  )).length;

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => firstInputRef.current?.focus());
  }, [open]);

  useEffect(() => {
    if (!open || boardId === null) return;
    void refreshScenarioHistory();
  }, [open, boardId]);

  useEffect(() => {
    if (!open) return;
    void refreshVoices();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  async function refreshScenarioHistory() {
    if (boardId === null) return;
    setLoadingHistory(true);
    try {
      const rows = await listScenarios(boardId);
      setScenarioHistory(rows);
      const ids = new Set(rows.map((scenario) => scenario.id));
      setSelectedScenarioIds((prev) => prev.filter((id) => ids.has(id)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load saved scenarios.");
    } finally {
      setLoadingHistory(false);
    }
  }

  function toggleScenarioSelection(scenarioId: number) {
    setSelectedScenarioIds((prev) => (
      prev.includes(scenarioId)
        ? prev.filter((id) => id !== scenarioId)
        : [...prev, scenarioId]
    ));
  }

  function selectVisibleScenarios() {
    setSelectedScenarioIds((prev) => (
      Array.from(new Set([...prev, ...filteredScenarioIds]))
    ));
  }

  function clearScenarioSelection() {
    setSelectedScenarioIds([]);
  }

  function setScenarioRunProgress(
    scenarioId: number,
    progress: ScenarioRunProgress[number],
  ) {
    setSelectedScenarioProgress((prev) => ({
      ...prev,
      [scenarioId]: progress,
    }));
  }

  function setScenarioActivity(
    scenarioId: number,
    activity: ScenarioActivity,
  ) {
    setScenarioActivities((prev) => ({
      ...prev,
      [scenarioId]: activity,
    }));
  }

  function updateScenarioActivity(
    scenarioId: number,
    patch: Partial<ScenarioActivity>,
  ) {
    setScenarioActivities((prev) => {
      const current = prev[scenarioId];
      if (!current) return prev;
      return {
        ...prev,
        [scenarioId]: { ...current, ...patch },
      };
    });
  }

  function tickScenarioActivity(scenarioId: number, total?: number) {
    setScenarioActivities((prev) => {
      const current = prev[scenarioId];
      if (!current) return prev;
      const nextTotal = total ?? current.total;
      return {
        ...prev,
        [scenarioId]: {
          ...current,
          total: nextTotal,
          done: Math.min(nextTotal, current.done + 1),
        },
      };
    });
  }

  function finishScenarioActivity(scenarioId: number, label = "Done") {
    setScenarioActivities((prev) => {
      const current = prev[scenarioId];
      const total = current?.total ?? 1;
      return {
        ...prev,
        [scenarioId]: {
          status: "done",
          label,
          done: total,
          total,
        },
      };
    });
  }

  function failScenarioActivity(scenarioId: number, error: string) {
    setScenarioActivities((prev) => {
      const current = prev[scenarioId];
      return {
        ...prev,
        [scenarioId]: {
          status: "error",
          label: current?.label ?? "Failed",
          done: current?.done ?? 0,
          total: current?.total ?? 1,
          error,
        },
      };
    });
  }

  function startEstimatedScenarioActivity(scenarioId: number, label: string) {
    setScenarioActivity(scenarioId, {
      status: "running",
      label,
      done: 5,
      total: 100,
    });
    const timer = window.setInterval(() => {
      setScenarioActivities((prev) => {
        const current = prev[scenarioId];
        if (!current || current.status !== "running") return prev;
        return {
          ...prev,
          [scenarioId]: {
            ...current,
            done: Math.min(92, current.done + 4),
          },
        };
      });
    }, 1400);
    return () => window.clearInterval(timer);
  }

  async function refreshVoices() {
    setLoadingVoices(true);
    try {
      const out = await listElevenLabsVoices();
      setVoices(out.voices);
      setSelectedVoiceId((prev) => (
        prev || result?.scenario.voice_id || (result ? "" : out.voices[0]?.voice_id || "")
      ));
      setBatchVoiceId((prev) => prev || out.voices[0]?.voice_id || "");
    } catch (err) {
      setVoices([]);
      setError(err instanceof Error ? err.message : "Could not load ElevenLabs voices.");
    } finally {
      setLoadingVoices(false);
    }
  }

  async function saveScenarioAudioSettings(mode: VideoAudioMode, voiceId: string) {
    if (boardId === null || result === null) return;
    const trimmed = voiceId.trim();
    if (mode === "veo_dialogue_elevenlabs_replace" && !trimmed) {
      setError("Choose an ElevenLabs voice for this Scenario.");
      return;
    }
    try {
      const out = await patchScenario(boardId, result.scenario.id, {
        video_audio_mode: mode,
        voice_id: mode === "veo_dialogue_elevenlabs_replace" ? trimmed : null,
      });
      setResult((prev) => (
        prev ? { ...prev, scenario: out.scenario } : prev
      ));
      setScenarioHistory((prev) => prev.map((scenario) => (
        scenario.id === out.scenario.id ? out.scenario : scenario
      )));
      setNotice("Scenario audio settings saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save Scenario audio settings.");
    }
  }

  function handleVoiceIdChange(value: string) {
    setSelectedVoiceId(value);
    setNotice(null);
    void saveScenarioAudioSettings(videoAudioMode, value);
  }

  function handleVideoAudioModeChange(mode: VideoAudioMode) {
    setVideoAudioMode(mode);
    setNotice(null);
    void saveScenarioAudioSettings(mode, selectedVoiceId);
  }

  async function handleBatchScan() {
    setError(null);
    setNotice(null);
    if (boardId === null) {
      setError("Board is not selected.");
      return;
    }
    const folderPath = batchFolderPath.trim();
    if (!folderPath) {
      setError("Enter a folder path first.");
      return;
    }
    try {
      const out = await scanScenarioBatchFolder(boardId, {
        folder_path: folderPath,
        input_type: batchInputType,
      });
      const mapped: BatchFileItem[] = out.items
        .filter((item): item is { name: string; path: string; kind: "image" | "video"; accepted: true } => (
          item.accepted && (item.kind === "image" || item.kind === "video")
        ))
        .map((item) => ({
          id: item.path,
          name: item.name,
          kind: item.kind,
          status: "ready",
          progress: 0,
          message: item.path,
        }));
      setBatchFiles(mapped);
      setNotice(`Scanned ${out.summary.total} files: accepted ${out.summary.accepted}, rejected ${out.summary.rejected}.`);
    } catch (err) {
      setBatchFiles([]);
      setError(err instanceof Error ? err.message : "Could not scan folder.");
    }
  }

  async function handleBatchGenerate() {
    setError(null);
    setNotice(null);
    if (boardId === null) {
      setError("Board is not selected.");
      return;
    }
    if (batchFiles.length === 0) {
      setError("No scanned files to generate scenarios from.");
      return;
    }
    if (!batchVoiceId) {
      setError("Choose a voice before generating scenarios.");
      return;
    }

    try {
      setBatchFiles((prev) => prev.map((file) => ({ ...file, status: "queued", progress: 10 })));
      const out = await createScenarioBatchRecords(boardId, {
        scene_count: batchSceneCount,
        voice_id: batchVoiceId,
        video_audio_mode: "veo_dialogue_elevenlabs_replace",
        items: batchFiles.map((file) => ({ path: file.message ?? file.name, kind: file.kind })),
      });

      const scenarioEntries = out.created.map((scenario) => {
        const refs = (scenario.refs ?? {}) as Record<string, unknown>;
        return {
          id: scenario.id,
          sourceType: refs.source_type,
          sourcePath: typeof refs.source_path === "string" ? refs.source_path : "",
        };
      });
      const imageScenarioEntries = scenarioEntries.filter((item) => item.sourceType === "image_file");
      const videoScenarioEntries = scenarioEntries.filter((item) => item.sourceType === "video_file");
      const imageScenarioIds = imageScenarioEntries.map((item) => item.id);
      const videoScenarioIds = videoScenarioEntries.map((item) => item.id);

      let failedCount = 0;
      const failedBySourcePath = new Map<string, string>();

      if (imageScenarioIds.length > 0) {
        setBatchFiles((prev) => prev.map((file) => (
          file.kind === "image"
            ? { ...file, status: "analyzing", progress: 55 }
            : file
        )));
        const analyzedImages = await analyzeBatchImageScenarios(boardId, { scenario_ids: imageScenarioIds });
        const failedByScenarioId = new Map(analyzedImages.failed.map((item) => [item.scenario_id, item.error]));
        for (const entry of imageScenarioEntries) {
          const failedReason = failedByScenarioId.get(entry.id);
          if (failedReason) failedBySourcePath.set(entry.sourcePath, failedReason);
        }
        failedCount += analyzedImages.failed.length;
      }

      if (videoScenarioIds.length > 0) {
        setBatchFiles((prev) => prev.map((file) => (
          file.kind === "video"
            ? { ...file, status: "transcribing", progress: 45 }
            : file
        )));
        setBatchFiles((prev) => prev.map((file) => (
          file.kind === "video"
            ? { ...file, status: "planning", progress: 70 }
            : file
        )));
        const analyzedVideos = await analyzeBatchVideoScenarios(boardId, { scenario_ids: videoScenarioIds });
        const failedByScenarioId = new Map(analyzedVideos.failed.map((item) => [item.scenario_id, item.error]));
        for (const entry of videoScenarioEntries) {
          const failedReason = failedByScenarioId.get(entry.id);
          if (failedReason) failedBySourcePath.set(entry.sourcePath, failedReason);
        }
        failedCount += analyzedVideos.failed.length;
      }

      setBatchFiles((prev) => prev.map((file) => {
        const sourcePath = file.message ?? "";
        const failedReason = failedBySourcePath.get(sourcePath);
        if (failedReason) {
          return { ...file, status: "error", progress: 100, message: failedReason };
        }
        return { ...file, status: "saved", progress: 100 };
      }));

      const rows = await listScenarios(boardId);
      setScenarioHistory(rows);

      setNotice(
        failedCount > 0
          ? `Created ${out.count} scenarios. Batch analysis failed: ${failedCount}.`
          : `Created ${out.count} scenarios and saved to Saved scenarios.`,
      );
    } catch (err) {
      setBatchFiles((prev) => prev.map((file) => ({ ...file, status: "error", progress: 100 })));
      setError(err instanceof Error ? err.message : "Could not create batch scenarios.");
    }
  }

  function resetScenarioForm() {
    setTheme("");
    setExtraDescription("");
    setSceneCount(5);
    setContentStyle("Premium fashion ad");
    setVideoAudioMode("silent");
    setBackgroundIds([]);
    setCharacterIds([]);
    setAssetIds([]);
    setPreview(null);
    setResult(null);
    setSceneDrafts([]);
    setBackgroundProgress({});
    setImageProgress({});
    setVideoProgress({});
    setGeneratingBackgrounds(false);
    setGeneratingImages(false);
    setGeneratingVideos(false);
    setGeneratingAll(false);
    setGenerateAllDone(0);
    setGenerateAllTotal(0);
    setGenerateAllLabel("");
    setExportingScenario(false);
  }

  function loadScenarioIntoDialog(loaded: ScenarioPlanResponse) {
    const scenes = [...loaded.scenes].sort((a, b) => a.idx - b.idx);
    const normalized = { ...loaded, scenes };
    const refs = loaded.scenario.refs ?? {};
    setResult(normalized);
    setSceneDrafts(scenes);
    setTheme(loaded.scenario.theme);
    setExtraDescription(loaded.scenario.extra_description ?? "");
    setSceneCount(loaded.scenario.scene_count);
    setContentStyle(asContentStyle(loaded.scenario.content_style));
    setVideoAudioMode(asVideoAudioMode(loaded.scenario.video_audio_mode));
    setSelectedVoiceId(loaded.scenario.voice_id ?? "");
    setBackgroundIds(mediaIdList(refs.background_media_ids));
    setCharacterIds(mediaIdList(refs.character_media_ids));
    setAssetIds(mediaIdList(refs.visual_asset_media_ids));
    setPreview(null);
    setBackgroundProgress({});
    setImageProgress({});
    setVideoProgress({});
    setGeneratingBackgrounds(false);
    setGeneratingImages(false);
    setGeneratingVideos(false);
    setGeneratingAll(false);
    setGenerateAllDone(0);
    setGenerateAllTotal(0);
    setGenerateAllLabel("");
    setExportingScenario(false);
  }

  async function handleNewScenario() {
    if (hasSceneEdits) {
      const saved = await saveSceneDrafts();
      if (saved === null) return;
    }
    setError(null);
    resetScenarioForm();
    setNotice("Ready for a new scenario.");
    requestAnimationFrame(() => firstInputRef.current?.focus());
  }

  async function handleOpenSavedScenario(scenarioId: number) {
    if (boardId === null) return;
    if (hasSceneEdits) {
      const saved = await saveSceneDrafts();
      if (saved === null) return;
    }
    setError(null);
    setNotice(null);
    setLoadingScenarioId(scenarioId);
    try {
      const loaded = await getScenario(boardId, scenarioId);
      loadScenarioIntoDialog(loaded);
      setNotice(`Loaded saved scenario #${scenarioId}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open saved scenario.");
    } finally {
      setLoadingScenarioId(null);
    }
  }

  async function handleDeleteSavedScenario(scenario: ScenarioDTO) {
    if (boardId === null) return;
    const confirmed = window.confirm(
      `Delete scenario #${scenario.id}? This removes it from saved scenarios.`,
    );
    if (!confirmed) return;

    setError(null);
    setNotice(null);
    setDeletingScenarioId(scenario.id);
    try {
      await deleteScenario(boardId, scenario.id);
      setScenarioHistory((prev) => prev.filter((item) => item.id !== scenario.id));
      setSelectedScenarioIds((prev) => prev.filter((id) => id !== scenario.id));
      setSelectedScenarioProgress((prev) => {
        const next = { ...prev };
        delete next[scenario.id];
        return next;
      });
      setScenarioActivities((prev) => {
        const next = { ...prev };
        delete next[scenario.id];
        return next;
      });
      if (result?.scenario.id === scenario.id) {
        resetScenarioForm();
      }
      setNotice(`Deleted scenario #${scenario.id}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete scenario.");
    } finally {
      setDeletingScenarioId(null);
    }
  }

  function buildPayload(): ScenarioPlanInput | null {
    const trimmedTheme = theme.trim();
    if (!trimmedTheme) {
      setError("Nhap chu de kich ban truoc khi generate script.");
      return null;
    }
    if (boardId === null) {
      setError("Board chua san sang de tao scenario.");
      return null;
    }
    if (sceneCount < 1) {
      setError("So luong canh phai lon hon 0.");
      return null;
    }
    const voiceId = selectedVoiceId.trim();
    if (videoAudioMode === "veo_dialogue_elevenlabs_replace" && !voiceId) {
      setError("Choose an ElevenLabs voice before creating this Scenario.");
      return null;
    }
    setError(null);
    return {
      theme: trimmedTheme,
      extra_description: extraDescription.trim(),
      scene_count: sceneCount,
      content_style: contentStyle,
      video_audio_mode: videoAudioMode,
      voice_id: videoAudioMode === "veo_dialogue_elevenlabs_replace" ? voiceId : null,
      refs: {
        background_media_ids: backgroundIds,
        character_media_ids: characterIds,
        visual_asset_media_ids: assetIds,
      },
    };
  }

  async function handleGenerateScript() {
    if (boardId === null) {
      setError("Board chua san sang de tao scenario.");
      return;
    }
    const payload = buildPayload();
    if (!payload) return;
    setPreview(payload);
    setResult(null);
    setSceneDrafts([]);
    setBackgroundProgress({});
    setImageProgress({});
    setVideoProgress({});
    setGenerateAllDone(0);
    setGenerateAllTotal(0);
    setGenerateAllLabel("");
    setSelectedGenerateDone(0);
    setSelectedGenerateTotal(0);
    setSelectedGenerateLabel("");
    setSelectedScenarioProgress({});
    setExportingScenario(false);
    setNotice(null);
    setSubmitting(true);
    try {
      const planned = await planScenario(boardId, payload);
      setResult(planned);
      setSceneDrafts(planned.scenes);
      setNotice("Scenario da duoc tao. Ban co the review va chinh tung scene.");
      void refreshScenarioHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generate script failed.");
    } finally {
      setSubmitting(false);
    }
  }

  function handleSceneDraftChange(sceneId: number, patch: Partial<ScenarioSceneDTO>) {
    setSceneDrafts((prev) => (
      prev.map((scene) => (scene.id === sceneId ? { ...scene, ...patch } : scene))
    ));
    setNotice(null);
  }

  async function saveSceneDrafts(): Promise<ScenarioSceneDTO[] | null> {
    if (boardId === null || result === null) return null;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await Promise.all(
        sceneDrafts.map((scene) => (
          patchScenarioScene(boardId, result.scenario.id, scene.id, {
            title: scene.title,
            background_description: scene.background_description,
            background_image_prompt: scene.background_image_prompt,
            composition_prompt: scene.composition_prompt,
            motion_prompt: scene.motion_prompt,
            voice_script: scene.voice_script,
            voice_direction: scene.voice_direction,
            duration_seconds: scene.duration_seconds,
          })
        )),
      );
      const sorted = [...saved].sort((a, b) => a.idx - b.idx);
      setSceneDrafts(sorted);
      setResult({ ...result, scenes: sorted });
      setNotice("Da luu thay doi scene.");
      void refreshScenarioHistory();
      return sorted;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save scene edits failed.");
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveSceneDrafts() {
    await saveSceneDrafts();
  }

  function applySavedScene(scene: ScenarioSceneDTO) {
    if (!isCurrentScenarioId(scene.scenario_id)) return;
    setSceneDrafts((prev) => (
      prev.map((item) => (item.id === scene.id ? scene : item))
        .sort((a, b) => a.idx - b.idx)
    ));
    setResult((prev) => (
      prev
        ? {
            ...prev,
            scenes: prev.scenes
              .map((item) => (item.id === scene.id ? scene : item))
              .sort((a, b) => a.idx - b.idx),
          }
        : prev
    ));
  }

  async function generateBackgroundScenes(
    boardIdValue: number,
    scenarioId: number,
    scenes: ScenarioSceneDTO[],
    projectId: string,
    onSettled?: () => void,
  ): Promise<{ scenes: ScenarioSceneDTO[]; failed: number }> {
    let failed = 0;
    const nextScenes = await Promise.all(
      scenes.map(async (scene) => {
        try {
          const prompt = scene.background_image_prompt.trim();
          if (!prompt) {
            failed += 1;
            const message = "missing background prompt";
            setBackgroundProgressForScenario(
              scenarioId,
              scene.id,
              { status: "error", error: message },
            );
            const updated = await patchScenarioScene(boardIdValue, scenarioId, scene.id, {
              status: "error",
              error: message,
            });
            applySavedScene(updated);
            return updated;
          }

          setBackgroundProgressForScenario(scenarioId, scene.id, { status: "queued" });
          const refs = mediaIdList(scene.refs.background_media_ids);
          const request = await createRequest({
            type: "gen_image",
            params: {
              prompt,
              project_id: projectId,
              aspect_ratio: "IMAGE_ASPECT_RATIO_LANDSCAPE",
              paygate_tier: paygateTier,
              variant_count: 1,
              image_model: imageModel,
              ...(refs.length > 0 ? { ref_media_ids: refs } : {}),
            },
          });
          setBackgroundProgressForScenario(scenarioId, scene.id, { status: "running" });
          const settled = await waitForRequestDone(request.id);
          if (settled.status !== "done") {
            throw new Error(settled.error ?? "background generation failed");
          }
          const mediaId = firstMediaId(settled.result.media_ids);
          if (!mediaId) throw new Error("background generation returned no media");

          const updated = await patchScenarioScene(boardIdValue, scenarioId, scene.id, {
            background_media_id: mediaId,
            image_media_id: null,
            video_media_id: null,
            status: "background_done",
            error: null,
          });
          applySavedScene(updated);
          setBackgroundProgressForScenario(scenarioId, scene.id, { status: "done" });
          return updated;
        } catch (err) {
          failed += 1;
          const message = err instanceof Error ? err.message : "background generation failed";
          setBackgroundProgressForScenario(
            scenarioId,
            scene.id,
            { status: "error", error: message },
          );
          try {
            const updated = await patchScenarioScene(boardIdValue, scenarioId, scene.id, {
              status: "error",
              error: message,
            });
            applySavedScene(updated);
            return updated;
          } catch {
            return scene;
          }
        } finally {
          onSettled?.();
        }
      }),
    );
    return { scenes: nextScenes, failed };
  }

  async function generateSceneImageScenes(
    boardIdValue: number,
    scenarioId: number,
    scenes: ScenarioSceneDTO[],
    projectId: string,
    onSettled?: () => void,
  ): Promise<{ scenes: ScenarioSceneDTO[]; failed: number }> {
    let failed = 0;
    const nextScenes = await Promise.all(
      scenes.map(async (scene) => {
        try {
          const prompt = scene.composition_prompt.trim();
          if (!prompt) {
            failed += 1;
            const message = "missing composition prompt";
            setImageProgressForScenario(
              scenarioId,
              scene.id,
              { status: "error", error: message },
            );
            const updated = await patchScenarioScene(boardIdValue, scenarioId, scene.id, {
              status: "error",
              error: message,
            });
            applySavedScene(updated);
            return updated;
          }

          setImageProgressForScenario(scenarioId, scene.id, { status: "queued" });
          const refs = compositionRefIds(scene);
          const request = await createRequest({
            type: "gen_image",
            params: {
              prompt,
              project_id: projectId,
              aspect_ratio: "IMAGE_ASPECT_RATIO_LANDSCAPE",
              paygate_tier: paygateTier,
              variant_count: 1,
              image_model: imageModel,
              ref_media_ids: refs,
            },
          });
          setImageProgressForScenario(scenarioId, scene.id, { status: "running" });
          const settled = await waitForRequestDone(request.id);
          if (settled.status !== "done") {
            throw new Error(settled.error ?? "scene image generation failed");
          }
          const mediaId = firstMediaId(settled.result.media_ids);
          if (!mediaId) throw new Error("scene image generation returned no media");

          const updated = await patchScenarioScene(boardIdValue, scenarioId, scene.id, {
            image_media_id: mediaId,
            video_media_id: null,
            status: "image_done",
            error: null,
          });
          applySavedScene(updated);
          setImageProgressForScenario(scenarioId, scene.id, { status: "done" });
          return updated;
        } catch (err) {
          failed += 1;
          const message = err instanceof Error ? err.message : "scene image generation failed";
          setImageProgressForScenario(
            scenarioId,
            scene.id,
            { status: "error", error: message },
          );
          try {
            const updated = await patchScenarioScene(boardIdValue, scenarioId, scene.id, {
              status: "error",
              error: message,
            });
            applySavedScene(updated);
            return updated;
          } catch {
            return scene;
          }
        } finally {
          onSettled?.();
        }
      }),
    );
    return { scenes: nextScenes, failed };
  }

  async function generateVideoScenes(
    boardIdValue: number,
    scenarioId: number,
    scenes: ScenarioSceneDTO[],
    projectId: string,
    mode: VideoAudioMode,
    onSettled?: () => void,
  ): Promise<{ scenes: ScenarioSceneDTO[]; failed: number }> {
    let failed = 0;
    const nextScenes = await Promise.all(
      scenes.map(async (scene) => {
        try {
          const { prompt, error: promptError } = videoPromptForScene(scene, mode);
          if (promptError) {
            failed += 1;
            const message = promptError;
            setVideoProgressForScenario(
              scenarioId,
              scene.id,
              { status: "error", error: message },
            );
            const updated = await patchScenarioScene(boardIdValue, scenarioId, scene.id, {
              status: "error",
              error: message,
            });
            applySavedScene(updated);
            return updated;
          }

          setVideoProgressForScenario(scenarioId, scene.id, { status: "queued" });
          const request = await createRequest({
            type: "gen_video",
            params: {
              prompt,
              project_id: projectId,
              start_media_id: scene.image_media_id,
              aspect_ratio: "VIDEO_ASPECT_RATIO_LANDSCAPE",
              paygate_tier: paygateTier,
              video_quality: videoQuality,
            },
          });
          setVideoProgressForScenario(scenarioId, scene.id, { status: "running" });
          const settled = await waitForRequestDone(request.id, 720);
          if (settled.status !== "done") {
            throw new Error(settled.error ?? "video generation failed");
          }
          const mediaId = firstMediaId(settled.result.media_ids);
          if (!mediaId) throw new Error("video generation returned no media");
          const partialError = typeof settled.result.partial_error === "string"
            ? settled.result.partial_error
            : null;

          const updated = await patchScenarioScene(boardIdValue, scenarioId, scene.id, {
            video_media_id: mediaId,
            status: "video_done",
            error: partialError,
          });
          applySavedScene(updated);
          setVideoProgressForScenario(
            scenarioId,
            scene.id,
            {
              status: "done",
              error: partialError ?? undefined,
            },
          );
          return updated;
        } catch (err) {
          failed += 1;
          const message = err instanceof Error ? err.message : "video generation failed";
          setVideoProgressForScenario(
            scenarioId,
            scene.id,
            { status: "error", error: message },
          );
          try {
            const updated = await patchScenarioScene(boardIdValue, scenarioId, scene.id, {
              status: "error",
              error: message,
            });
            applySavedScene(updated);
            return updated;
          } catch {
            return scene;
          }
        } finally {
          onSettled?.();
        }
      }),
    );
    return { scenes: nextScenes, failed };
  }

  async function handleGenerateBackgrounds() {
    if (boardId === null || result === null) return;
    const scenarioId = result.scenario.id;
    if (!paygateTier) {
      setError("Open Flow once so the extension can detect your plan, then retry.");
      return;
    }
    setError(null);
    setNotice(null);

    const savedScenes = hasSceneEdits ? await saveSceneDrafts() : sceneDrafts;
    if (savedScenes === null) return;

    const projectId = await ensureProjectId();
    if (projectId === null) {
      setError("Could not prepare Flow project for background generation.");
      return;
    }

    setGeneratingBackgrounds(true);
    setBackgroundProgress({});
    setImageProgress({});
    setVideoProgress({});
    setScenarioActivity(scenarioId, {
      status: "running",
      label: "Generating backgrounds",
      done: 0,
      total: savedScenes.length,
    });
    let failed = 0;

    await Promise.all(
      savedScenes.map(async (scene) => {
        try {
          const prompt = scene.background_image_prompt.trim();
          if (!prompt) {
            failed += 1;
            setBackgroundProgressForScenario(
              scenarioId,
              scene.id,
              { status: "error", error: "missing background prompt" },
            );
            return;
          }
          setBackgroundProgressForScenario(scenarioId, scene.id, { status: "queued" });
          const refs = mediaIdList(scene.refs.background_media_ids);
          const request = await createRequest({
            type: "gen_image",
            params: {
              prompt,
              project_id: projectId,
              aspect_ratio: "IMAGE_ASPECT_RATIO_LANDSCAPE",
              paygate_tier: paygateTier,
              variant_count: 1,
              image_model: imageModel,
              ...(refs.length > 0 ? { ref_media_ids: refs } : {}),
            },
          });
          setBackgroundProgressForScenario(scenarioId, scene.id, { status: "running" });
          const settled = await waitForRequestDone(request.id);
          if (settled.status !== "done") {
            throw new Error(settled.error ?? "background generation failed");
          }
          const mediaId = firstMediaId(settled.result.media_ids);
          if (!mediaId) throw new Error("background generation returned no media");

          const updated = await patchScenarioScene(boardId, scenarioId, scene.id, {
            background_media_id: mediaId,
            image_media_id: null,
            video_media_id: null,
            status: "background_done",
            error: null,
          });
          applySavedScene(updated);
          setBackgroundProgressForScenario(scenarioId, scene.id, { status: "done" });
        } catch (err) {
          failed += 1;
          const message = err instanceof Error ? err.message : "background generation failed";
          setBackgroundProgressForScenario(
            scenarioId,
            scene.id,
            { status: "error", error: message },
          );
          try {
            const updated = await patchScenarioScene(boardId, scenarioId, scene.id, {
              status: "error",
              error: message,
            });
            applySavedScene(updated);
          } catch {
            // Keep the local progress error; persistence failure is secondary.
          }
        } finally {
          tickScenarioActivity(scenarioId, savedScenes.length);
        }
      }),
    );

    finishScenarioActivity(
      scenarioId,
      failed > 0 ? `Backgrounds finished with ${failed} failed` : "Backgrounds complete",
    );
    if (isCurrentScenarioId(scenarioId)) {
      setGeneratingBackgrounds(false);
      setNotice(
        failed > 0
          ? `Background generation finished with ${failed} failed scene(s).`
          : "All scene backgrounds generated.",
      );
    }
    void refreshScenarioHistory();
  }

  async function handleGenerateSceneImages() {
    if (boardId === null || result === null) return;
    const scenarioId = result.scenario.id;
    if (!paygateTier) {
      setError("Open Flow once so the extension can detect your plan, then retry.");
      return;
    }
    setError(null);
    setNotice(null);

    const savedScenes = hasSceneEdits ? await saveSceneDrafts() : sceneDrafts;
    if (savedScenes === null) return;

    const missingBackgrounds = savedScenes.filter((scene) => !scene.background_media_id);
    if (missingBackgrounds.length > 0) {
      setError(
        `Generate backgrounds for all scenes before scene images (${missingBackgrounds.length} missing).`,
      );
      return;
    }

    const projectId = await ensureProjectId();
    if (projectId === null) {
      setError("Could not prepare Flow project for scene image generation.");
      return;
    }

    setGeneratingImages(true);
    setImageProgress({});
    setVideoProgress({});
    setScenarioActivity(scenarioId, {
      status: "running",
      label: "Generating scene images",
      done: 0,
      total: savedScenes.length,
    });
    let failed = 0;

    await Promise.all(
      savedScenes.map(async (scene) => {
        try {
          const prompt = scene.composition_prompt.trim();
          if (!prompt) {
            failed += 1;
            const message = "missing composition prompt";
            setImageProgressForScenario(
              scenarioId,
              scene.id,
              { status: "error", error: message },
            );
            try {
              const updated = await patchScenarioScene(boardId, scenarioId, scene.id, {
                status: "error",
                error: message,
              });
              applySavedScene(updated);
            } catch {
              // Keep the local progress error; persistence failure is secondary.
            }
            return;
          }
          setImageProgressForScenario(scenarioId, scene.id, { status: "queued" });
          const refs = compositionRefIds(scene);
          const request = await createRequest({
            type: "gen_image",
            params: {
              prompt,
              project_id: projectId,
              aspect_ratio: "IMAGE_ASPECT_RATIO_LANDSCAPE",
              paygate_tier: paygateTier,
              variant_count: 1,
              image_model: imageModel,
              ref_media_ids: refs,
            },
          });
          setImageProgressForScenario(scenarioId, scene.id, { status: "running" });
          const settled = await waitForRequestDone(request.id);
          if (settled.status !== "done") {
            throw new Error(settled.error ?? "scene image generation failed");
          }
          const mediaId = firstMediaId(settled.result.media_ids);
          if (!mediaId) throw new Error("scene image generation returned no media");

          const updated = await patchScenarioScene(boardId, scenarioId, scene.id, {
            image_media_id: mediaId,
            video_media_id: null,
            status: "image_done",
            error: null,
          });
          applySavedScene(updated);
          setImageProgressForScenario(scenarioId, scene.id, { status: "done" });
        } catch (err) {
          failed += 1;
          const message = err instanceof Error ? err.message : "scene image generation failed";
          setImageProgressForScenario(
            scenarioId,
            scene.id,
            { status: "error", error: message },
          );
          try {
            const updated = await patchScenarioScene(boardId, scenarioId, scene.id, {
              status: "error",
              error: message,
            });
            applySavedScene(updated);
          } catch {
            // Keep the local progress error; persistence failure is secondary.
          }
        } finally {
          tickScenarioActivity(scenarioId, savedScenes.length);
        }
      }),
    );

    finishScenarioActivity(
      scenarioId,
      failed > 0 ? `Scene images finished with ${failed} failed` : "Scene images complete",
    );
    if (isCurrentScenarioId(scenarioId)) {
      setGeneratingImages(false);
      setNotice(
        failed > 0
          ? `Scene image generation finished with ${failed} failed scene(s).`
          : "All scene images generated.",
      );
    }
    void refreshScenarioHistory();
  }

  async function handleGenerateVideos() {
    if (boardId === null || result === null) return;
    const scenarioId = result.scenario.id;
    if (!paygateTier) {
      setError("Open Flow once so the extension can detect your plan, then retry.");
      return;
    }
    setError(null);
    setNotice(null);

    const savedScenes = hasSceneEdits ? await saveSceneDrafts() : sceneDrafts;
    if (savedScenes === null) return;

    const missingImages = savedScenes.filter((scene) => !scene.image_media_id);
    if (missingImages.length > 0) {
      setError(
        `Generate scene images for all scenes before videos (${missingImages.length} missing).`,
      );
      return;
    }

    const projectId = await ensureProjectId();
    if (projectId === null) {
      setError("Could not prepare Flow project for video generation.");
      return;
    }

    setGeneratingVideos(true);
    setVideoProgress({});
    setScenarioActivity(scenarioId, {
      status: "running",
      label: "Generating videos",
      done: 0,
      total: savedScenes.length,
    });
    let failed = 0;

    await Promise.all(
      savedScenes.map(async (scene) => {
        try {
          const { prompt, error: promptError } = videoPromptForScene(scene, videoAudioMode);
          if (promptError) {
            failed += 1;
            const message = promptError;
            setVideoProgressForScenario(
              scenarioId,
              scene.id,
              { status: "error", error: message },
            );
            try {
              const updated = await patchScenarioScene(boardId, scenarioId, scene.id, {
                status: "error",
                error: message,
              });
              applySavedScene(updated);
            } catch {
              // Keep the local progress error; persistence failure is secondary.
            }
            return;
          }
          setVideoProgressForScenario(scenarioId, scene.id, { status: "queued" });
          const request = await createRequest({
            type: "gen_video",
            params: {
              prompt,
              project_id: projectId,
              start_media_id: scene.image_media_id,
              aspect_ratio: "VIDEO_ASPECT_RATIO_LANDSCAPE",
              paygate_tier: paygateTier,
              video_quality: videoQuality,
            },
          });
          setVideoProgressForScenario(scenarioId, scene.id, { status: "running" });
          const settled = await waitForRequestDone(request.id, 720);
          if (settled.status !== "done") {
            throw new Error(settled.error ?? "video generation failed");
          }
          const mediaId = firstMediaId(settled.result.media_ids);
          if (!mediaId) throw new Error("video generation returned no media");
          const partialError = typeof settled.result.partial_error === "string"
            ? settled.result.partial_error
            : null;

          const updated = await patchScenarioScene(boardId, scenarioId, scene.id, {
            video_media_id: mediaId,
            status: "video_done",
            error: partialError,
          });
          applySavedScene(updated);
          setVideoProgressForScenario(
            scenarioId,
            scene.id,
            {
              status: "done",
              error: partialError ?? undefined,
            },
          );
        } catch (err) {
          failed += 1;
          const message = err instanceof Error ? err.message : "video generation failed";
          setVideoProgressForScenario(
            scenarioId,
            scene.id,
            { status: "error", error: message },
          );
          try {
            const updated = await patchScenarioScene(boardId, scenarioId, scene.id, {
              status: "error",
              error: message,
            });
            applySavedScene(updated);
          } catch {
            // Keep the local progress error; persistence failure is secondary.
          }
        } finally {
          tickScenarioActivity(scenarioId, savedScenes.length);
        }
      }),
    );

    finishScenarioActivity(
      scenarioId,
      failed > 0 ? `Videos finished with ${failed} failed` : "Videos complete",
    );
    if (isCurrentScenarioId(scenarioId)) {
      setGeneratingVideos(false);
      setNotice(
        failed > 0
          ? `Video generation finished with ${failed} failed scene(s).`
          : "All scene videos generated.",
      );
    }
    void refreshScenarioHistory();
  }

  async function handleGenerateAll() {
    if (boardId === null || result === null) return;
    const scenarioId = result.scenario.id;
    setError(null);
    setNotice(null);

    const savedScenes = hasSceneEdits ? await saveSceneDrafts() : sceneDrafts;
    if (savedScenes === null) return;
    setNotice(null);

    const missingDialogue = videoAudioMode === "silent"
      ? []
      : savedScenes.filter((scene) => scene.voice_script.trim().length === 0);
    if (missingDialogue.length > 0) {
      setError(`Add voice script for ${missingDialogue.length} scene(s) before Generate all.`);
      return;
    }
    const totalSteps = remainingAutoStepCount(savedScenes, false);
    if (totalSteps === 0) {
      setNotice("Scenario is already fully generated.");
      return;
    }

    const needsFlow = savedScenes.some((scene) => (
      !scene.background_media_id || !scene.image_media_id || !scene.video_media_id
    ));
    if (needsFlow && !paygateTier) {
      setError("Open Flow once so the extension can detect your plan, then retry.");
      return;
    }

    let projectId: string | null = null;
    if (needsFlow) {
      projectId = await ensureProjectId();
      if (projectId === null) {
        setError("Could not prepare Flow project for Generate all.");
        return;
      }
    }

    const tick = () => {
      tickScenarioActivity(scenarioId, totalSteps);
      if (isCurrentScenarioId(scenarioId)) {
        setGenerateAllDone((prev) => Math.min(totalSteps, prev + 1));
      }
    };

    setGeneratingAll(true);
    setGenerateAllDone(0);
    setGenerateAllTotal(totalSteps);
    setGenerateAllLabel("Preparing");
    setBackgroundProgress({});
    setImageProgress({});
    setVideoProgress({});
    setScenarioActivity(scenarioId, {
      status: "running",
      label: "Generate all",
      done: 0,
      total: totalSteps,
    });

    let workingScenes = savedScenes;
    let failed = 0;
    try {
      const backgroundTargets = workingScenes.filter((scene) => !scene.background_media_id);
      if (backgroundTargets.length > 0 && projectId !== null) {
        if (isCurrentScenarioId(scenarioId)) {
          setGenerateAllLabel("Generating backgrounds");
          setGeneratingBackgrounds(true);
        }
        updateScenarioActivity(scenarioId, { label: "Generating backgrounds" });
        const out = await generateBackgroundScenes(
          boardId,
          scenarioId,
          backgroundTargets,
          projectId,
          tick,
        );
        failed += out.failed;
        workingScenes = mergeScenesById(workingScenes, out.scenes);
        if (isCurrentScenarioId(scenarioId)) {
          setGeneratingBackgrounds(false);
        }
      }

      const imageTargets = workingScenes.filter((scene) => (
        scene.background_media_id && !scene.image_media_id
      ));
      if (imageTargets.length > 0 && projectId !== null) {
        if (isCurrentScenarioId(scenarioId)) {
          setGenerateAllLabel("Generating scene images");
          setGeneratingImages(true);
        }
        updateScenarioActivity(scenarioId, { label: "Generating scene images" });
        const out = await generateSceneImageScenes(
          boardId,
          scenarioId,
          imageTargets,
          projectId,
          tick,
        );
        failed += out.failed;
        workingScenes = mergeScenesById(workingScenes, out.scenes);
        if (isCurrentScenarioId(scenarioId)) {
          setGeneratingImages(false);
        }
      }

      const videoTargets = workingScenes.filter((scene) => scene.image_media_id && !scene.video_media_id);
      if (videoTargets.length > 0 && projectId !== null) {
        if (isCurrentScenarioId(scenarioId)) {
          setGenerateAllLabel("Generating videos");
          setGeneratingVideos(true);
        }
        updateScenarioActivity(scenarioId, { label: "Generating videos" });
        const out = await generateVideoScenes(
          boardId,
          scenarioId,
          videoTargets,
          projectId,
          videoAudioMode,
          tick,
        );
        failed += out.failed;
        workingScenes = mergeScenesById(workingScenes, out.scenes);
        if (isCurrentScenarioId(scenarioId)) {
          setGeneratingVideos(false);
        }
      }

      if (isCurrentScenarioId(scenarioId)) {
        setGenerateAllDone(totalSteps);
        setNotice(
          failed > 0
            ? `Generate all finished with ${failed} failed step(s).`
          : "Generate all complete.",
        );
      }
      finishScenarioActivity(
        scenarioId,
        failed > 0 ? `Generate all finished with ${failed} failed` : "Generate all complete",
      );
    } catch (err) {
      failScenarioActivity(
        scenarioId,
        err instanceof Error ? err.message : "Generate all failed.",
      );
      if (isCurrentScenarioId(scenarioId)) {
        setError(err instanceof Error ? err.message : "Generate all failed.");
      }
    } finally {
      if (isCurrentScenarioId(scenarioId)) {
        setGeneratingBackgrounds(false);
        setGeneratingImages(false);
        setGeneratingVideos(false);
        setGeneratingAll(false);
        setGenerateAllLabel("");
        setGenerateAllDone(totalSteps);
      }
      void refreshScenarioHistory();
    }
  }

  async function runScenarioAutoFlow(
    boardIdValue: number,
    loaded: ScenarioPlanResponse,
    projectId: string | null,
    onLabel: (label: string) => void,
  ): Promise<number> {
    const scenarioMode = asVideoAudioMode(loaded.scenario.video_audio_mode);
    let workingScenes = [...loaded.scenes].sort((a, b) => a.idx - b.idx);
    let failed = 0;

    const missingDialogue = scenarioMode === "silent"
      ? []
      : workingScenes.filter((scene) => scene.voice_script.trim().length === 0);
    if (missingDialogue.length > 0) {
      throw new Error(`missing voice script for ${missingDialogue.length} scene(s)`);
    }

    const scenarioId = loaded.scenario.id;
    const backgroundTargets = workingScenes.filter((scene) => !scene.background_media_id);
    if (backgroundTargets.length > 0) {
      if (projectId === null) throw new Error("Flow project is not ready");
      onLabel("backgrounds");
      const out = await generateBackgroundScenes(
        boardIdValue,
        scenarioId,
        backgroundTargets,
        projectId,
      );
      failed += out.failed;
      workingScenes = mergeScenesById(workingScenes, out.scenes);
    }

    const imageTargets = workingScenes.filter((scene) => (
      scene.background_media_id && !scene.image_media_id
    ));
    if (imageTargets.length > 0) {
      if (projectId === null) throw new Error("Flow project is not ready");
      onLabel("scene images");
      const out = await generateSceneImageScenes(
        boardIdValue,
        scenarioId,
        imageTargets,
        projectId,
      );
      failed += out.failed;
      workingScenes = mergeScenesById(workingScenes, out.scenes);
    }

    const videoTargets = workingScenes.filter((scene) => (
      scene.image_media_id && !scene.video_media_id
    ));
    if (videoTargets.length > 0) {
      if (projectId === null) throw new Error("Flow project is not ready");
      onLabel("videos");
      const out = await generateVideoScenes(
        boardIdValue,
        scenarioId,
        videoTargets,
        projectId,
        scenarioMode,
      );
      failed += out.failed;
      workingScenes = mergeScenesById(workingScenes, out.scenes);
    }

    return failed;
  }

  async function handleGenerateSelected() {
    if (boardId === null) return;
    const ids = selectedScenarioIds.filter((id) => (
      scenarioHistory.some((scenario) => scenario.id === id)
    ));
    if (ids.length === 0) {
      setError("Select at least one scenario first.");
      return;
    }

    setError(null);
    setNotice(null);
    if (hasSceneEdits) {
      const saved = await saveSceneDrafts();
      if (saved === null) return;
      setNotice(null);
    }

    const rows = scenarioHistory.filter((scenario) => ids.includes(scenario.id));

    const needsFlow = rows.some((scenario) => scenarioStage(scenario) !== "video_done");
    if (needsFlow && !paygateTier) {
      setError("Open Flow once so the extension can detect your plan, then retry.");
      return;
    }

    let projectId: string | null = null;
    if (needsFlow) {
      projectId = await ensureProjectId();
      if (projectId === null) {
        setError("Could not prepare Flow project for Generate selected.");
        return;
      }
    }

    let failedScenarios = 0;
    setGeneratingSelected(true);
    setSelectedGenerateDone(0);
    setSelectedGenerateTotal(ids.length);
    setSelectedGenerateLabel("Preparing selected scenarios");
    setSelectedScenarioProgress(Object.fromEntries(
      ids.map((id) => [id, { status: "queued" as const, label: "Queued" }]),
    ));
    for (const id of ids) {
      setScenarioActivity(id, {
        status: "queued",
        label: "Queued",
        done: 0,
        total: 100,
      });
    }

    try {
      await runLimited(ids, 3, async (scenarioId) => {
        setScenarioRunProgress(scenarioId, { status: "running", label: "Loading" });
        updateScenarioActivity(scenarioId, {
          status: "running",
          label: "Loading",
          done: 8,
          total: 100,
        });
        setSelectedGenerateLabel(`Scenario #${scenarioId}: loading`);
        try {
          const loaded = await getScenario(boardId, scenarioId);
          if (remainingAutoStepCount(loaded.scenes, false) === 0) {
            setScenarioRunProgress(scenarioId, { status: "done", label: "Already complete" });
            finishScenarioActivity(scenarioId, "Already complete");
            return;
          }
          const failedSteps = await runScenarioAutoFlow(
            boardId,
            loaded,
            projectId,
            (label) => {
              setScenarioRunProgress(scenarioId, { status: "running", label });
              updateScenarioActivity(scenarioId, {
                status: "running",
                label,
                done: label === "backgrounds" ? 25 : label === "scene images" ? 55 : 82,
                total: 100,
              });
              setSelectedGenerateLabel(`Scenario #${scenarioId}: ${label}`);
            },
          );
          if (failedSteps > 0) {
            failedScenarios += 1;
            setScenarioRunProgress(scenarioId, {
              status: "error",
              error: `${failedSteps} failed step(s)`,
            });
            failScenarioActivity(scenarioId, `${failedSteps} failed step(s)`);
          } else {
            setScenarioRunProgress(scenarioId, { status: "done", label: "Done" });
            finishScenarioActivity(scenarioId, "Generate complete");
          }
        } catch (err) {
          failedScenarios += 1;
          const message = err instanceof Error ? err.message : "Generate selected failed";
          setScenarioRunProgress(scenarioId, {
            status: "error",
            error: message,
          });
          failScenarioActivity(scenarioId, message);
        } finally {
          setSelectedGenerateDone((prev) => Math.min(ids.length, prev + 1));
        }
      });
      setNotice(
        failedScenarios > 0
          ? `Generate selected finished with ${failedScenarios} failed scenario(s).`
          : "Generate selected complete.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generate selected failed.");
    } finally {
      setGeneratingSelected(false);
      setSelectedGenerateLabel("");
      setSelectedGenerateDone(ids.length);
      void refreshScenarioHistory();
    }
  }

  async function handleExportScenario() {
    if (boardId === null || result === null) return;
    const scenarioId = result.scenario.id;
    setError(null);
    setNotice(null);
    const savedScenes = hasSceneEdits ? await saveSceneDrafts() : sceneDrafts;
    if (savedScenes === null) return;
    const missingVideos = savedScenes.filter((scene) => !scene.video_media_id);
    if (missingVideos.length > 0) {
      setError(`Generate videos for all scenes before export (${missingVideos.length} missing).`);
      return;
    }

    setExportingScenario(true);
    const stopExportEstimate = startEstimatedScenarioActivity(
      scenarioId,
      usesElevenLabsVoice ? "Changing voice and exporting" : "Exporting scenario",
    );
    try {
      const out = await exportScenario(boardId, scenarioId);
      if (isCurrentScenarioId(scenarioId)) {
        setResult((prev) => (
          prev ? { ...prev, scenario: out.scenario } : prev
        ));
        setNotice(`Scenario exported. Folder: ${out.export_dir}`);
      }
      finishScenarioActivity(scenarioId, "Export complete");
      void refreshScenarioHistory();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Export scenario failed.";
      failScenarioActivity(scenarioId, message);
      if (isCurrentScenarioId(scenarioId)) {
        setError(message);
      }
    } finally {
      stopExportEstimate();
      if (isCurrentScenarioId(scenarioId)) {
        setExportingScenario(false);
      }
    }
  }

  async function handleExportSelectedScenarios() {
    if (boardId === null) return;
    const ids = selectedScenarioIds.filter((id) => (
      scenarioHistory.some((scenario) => scenario.id === id)
    ));
    if (ids.length === 0) {
      setError("Select at least one scenario first.");
      return;
    }

    setError(null);
    setNotice(null);
    setExportingSelected(true);
    setSelectedScenarioProgress(Object.fromEntries(
      ids.map((id) => [id, { status: "queued" as const, label: "Export queued" }]),
    ));
    const stopExportEstimates = ids.map((id) => (
      startEstimatedScenarioActivity(id, "Export queued")
    ));

    try {
      for (const id of ids) {
        updateScenarioActivity(id, {
          status: "running",
          label: selectedNeedsElevenLabsVoice ? "Changing voice and exporting" : "Exporting",
          done: 8,
          total: 100,
        });
      }
      const out = await exportSelectedScenarios(boardId, ids);
      const exportedById = new Map(
        out.exported.map((item) => [item.scenario_id, item.scenario]),
      );
      setScenarioHistory((prev) => prev.map((scenario) => (
        exportedById.get(scenario.id) ?? scenario
      )));
      setResult((prev) => {
        if (!prev) return prev;
        const exportedScenario = exportedById.get(prev.scenario.id);
        return exportedScenario ? { ...prev, scenario: exportedScenario } : prev;
      });
      for (const item of out.exported) {
        setScenarioRunProgress(item.scenario_id, { status: "done", label: "Exported" });
        finishScenarioActivity(item.scenario_id, "Exported");
      }
      for (const item of out.failed) {
        setScenarioRunProgress(item.scenario_id, {
          status: "error",
          error: item.error,
        });
        failScenarioActivity(item.scenario_id, item.error);
      }
      const failedText = out.failed.length > 0
        ? `, ${out.failed.length} failed`
        : "";
      setNotice(
        `Exported ${out.exported.length} selected scenario(s)${failedText}. Folder: ${out.export_dir}`,
      );
      void refreshScenarioHistory();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Export selected scenarios failed.";
      for (const id of ids) {
        setScenarioRunProgress(id, { status: "error", error: message });
        failScenarioActivity(id, message);
      }
      setError(message);
    } finally {
      for (const stop of stopExportEstimates) stop();
      setExportingSelected(false);
    }
  }

  const hasSceneEdits = result !== null && (
    JSON.stringify(sceneDrafts.map(editableSceneShape))
    !== JSON.stringify(result.scenes.map(editableSceneShape))
  );
  const currentScenarioActivity = result ? scenarioActivities[result.scenario.id] : undefined;
  const currentScenarioActivityPercent = activityPercent(currentScenarioActivity);
  const currentScenarioActivityBusy = isRunningActivity(currentScenarioActivity);
  const currentScenarioBusy = (
    saving
    || generatingBackgrounds
    || generatingImages
    || generatingVideos
    || generatingAll
    || exportingScenario
    || currentScenarioActivityBusy
  );
  const selectedBatchBusy = generatingSelected || exportingSelected;
  const generationActionBusy = currentScenarioBusy || generatingSelected;
  const canStartNewScenario = !submitting && !saving;
  const canOpenHistoryScenario = !submitting && !saving && loadingScenarioId === null;
  const canSubmitScript = (
    !submitting
    && !saving
    && (
      videoAudioMode !== "veo_dialogue_elevenlabs_replace"
      || selectedVoiceId.trim().length > 0
    )
    && (result === null || !currentScenarioBusy)
  );
  const submitScriptDisabledReason = (
    videoAudioMode === "veo_dialogue_elevenlabs_replace"
    && selectedVoiceId.trim().length === 0
  )
    ? "Choose an ElevenLabs voice first."
    : "";
  const scenarioSceneCount = sceneDrafts.length;
  const backgroundDoneCount = sceneDrafts.filter((scene) => scene.background_media_id).length;
  const imageDoneCount = sceneDrafts.filter((scene) => scene.image_media_id).length;
  const videoDoneCount = sceneDrafts.filter((scene) => scene.video_media_id).length;
  const missingBackgroundCount = Math.max(0, scenarioSceneCount - backgroundDoneCount);
  const missingImageCount = Math.max(0, scenarioSceneCount - imageDoneCount);
  const missingVideoCount = Math.max(0, scenarioSceneCount - videoDoneCount);
  const missingDialogueScriptCount = videoAudioMode === "silent"
    ? 0
    : sceneDrafts.filter((scene) => scene.voice_script.trim().length === 0).length;
  const hasScenario = result !== null && scenarioSceneCount > 0;
  const allBackgroundsDone = hasScenario && missingBackgroundCount === 0;
  const allImagesDone = hasScenario && missingImageCount === 0;
  const usesElevenLabsVoice = videoAudioMode === "veo_dialogue_elevenlabs_replace";
  const selectedScenarios = scenarioHistory.filter((scenario) => (
    selectedScenarioIds.includes(scenario.id)
  ));
  const selectedActivityItems = selectedScenarioIds
    .map((id) => scenarioActivities[id])
    .filter((activity): activity is ScenarioActivity => Boolean(activity));
  const selectedActivityBusy = selectedActivityItems.some(isRunningActivity);
  const selectedNeedsElevenLabsVoice = selectedScenarios.some((scenario) => (
    asVideoAudioMode(scenario.video_audio_mode) === "veo_dialogue_elevenlabs_replace"
  ));
  const selectedMissingVoiceCount = selectedScenarios.filter((scenario) => (
    asVideoAudioMode(scenario.video_audio_mode) === "veo_dialogue_elevenlabs_replace"
    && !scenario.voice_id
  )).length;
  const savedScenarioVoiceId = result?.scenario.voice_id?.trim() ?? "";
  const voicePickerEnabled = usesElevenLabsVoice;
  const canGenerateBackgrounds = hasScenario && !generationActionBusy;
  const canGenerateSceneImages = hasScenario && allBackgroundsDone && !generationActionBusy;
  const canGenerateVideos = (
    hasScenario
    && allImagesDone
    && missingDialogueScriptCount === 0
    && !generationActionBusy
  );
  const autoRemainingStepCount = remainingAutoStepCount(sceneDrafts, false);
  const canGenerateAll = (
    hasScenario
    && autoRemainingStepCount > 0
    && missingDialogueScriptCount === 0
    && !generationActionBusy
  );
  const selectedRemainingScenarioCount = selectedScenarios.filter(scenarioHasRemainingWork).length;
  const canGenerateSelected = (
    selectedScenarioIds.length > 0
    && selectedRemainingScenarioCount > 0
    && !selectedBatchBusy
    && !selectedActivityBusy
    && !submitting
    && !saving
  );
  const sceneImagesDisabledReason = !hasScenario
    ? "Generate a script first."
    : allBackgroundsDone
      ? ""
      : `Need ${missingBackgroundCount} background(s) first.`;
  const videosDisabledReason = !hasScenario
    ? "Generate a script first."
    : !allImagesDone
      ? `Need ${missingImageCount} scene image(s) first.`
      : missingDialogueScriptCount > 0
        ? `Need voice script for ${missingDialogueScriptCount} scene(s).`
        : "";
  const generateAllDisabledReason = !hasScenario
    ? "Generate a script first."
    : autoRemainingStepCount === 0
      ? "Scenario is already fully generated."
      : missingDialogueScriptCount > 0
        ? `Need voice script for ${missingDialogueScriptCount} scene(s).`
        : "";
  const generateSelectedDisabledReason = selectedScenarioIds.length === 0
    ? "Select at least one scenario."
    : selectedActivityBusy
      ? "Selected scenario is already running."
    : selectedRemainingScenarioCount === 0
      ? "Selected scenarios are already generated."
      : "";
  const canExportScenario = (
    hasScenario
    && missingVideoCount === 0
    && (!usesElevenLabsVoice || savedScenarioVoiceId.length > 0)
    && !currentScenarioBusy
    && !generatingSelected
  );
  const exportScenarioDisabledReason = !hasScenario
    ? "Generate a script first."
    : missingVideoCount > 0
      ? `Need ${missingVideoCount} video(s) first.`
      : usesElevenLabsVoice && !savedScenarioVoiceId
        ? "Choose and save an ElevenLabs voice for this Scenario."
      : "";
  const canExportSelected = (
    selectedScenarios.length > 0
    && selectedMissingVoiceCount === 0
    && !selectedBatchBusy
    && !selectedActivityBusy
    && !submitting
    && !saving
  );
  const exportSelectedDisabledReason = selectedScenarioIds.length === 0
    ? "Select at least one scenario."
    : selectedActivityBusy
      ? "Selected scenario is already running."
    : selectedMissingVoiceCount > 0
      ? `${selectedMissingVoiceCount} selected scenario(s) need saved voice.`
    : "";
  const manualFlowHint = result === null
    ? ""
    : missingBackgroundCount > 0
      ? `Manual flow: generate backgrounds first (${missingBackgroundCount} missing).`
      : missingImageCount > 0
        ? `Manual flow: generate scene images next (${missingImageCount} missing).`
        : missingVideoCount > 0
          ? `Manual flow: generate videos next (${missingVideoCount} missing).`
          : "Manual flow complete.";
  const generateAllPercent = generateAllTotal > 0
    ? Math.round((generateAllDone / generateAllTotal) * 100)
    : 0;
  const selectedGeneratePercent = selectedGenerateTotal > 0
    ? Math.round((selectedGenerateDone / selectedGenerateTotal) * 100)
    : 0;
  const selectedActivityPercent = selectedActivityItems.length > 0
    ? Math.round(
        selectedActivityItems.reduce((sum, activity) => (
          sum + activityPercent(activity)
        ), 0) / selectedActivityItems.length,
      )
    : 0;
  const selectedActivityLabel = (
    selectedActivityItems.find(isRunningActivity)?.label
    ?? selectedActivityItems[0]?.label
    ?? ""
  );
  const activeStatusText = (
    generatingSelected
      ? `${selectedGenerateLabel || "Generating selected"}... ${selectedGeneratePercent}%`
      : exportingSelected
        ? `${selectedActivityLabel || "Exporting selected scenarios"}... ${selectedActivityPercent}%`
        : currentScenarioActivity && currentScenarioActivityBusy
          ? `${currentScenarioActivity.label}... ${currentScenarioActivityPercent}%`
      : generatingAll
        ? `${generateAllLabel || "Generating all"}... ${generateAllPercent}%`
        : exportingScenario
          ? "Exporting scenario... 0%"
          : generatingVideos
              ? "Dang generate videos... 0%"
              : generatingImages
                ? "Dang generate scene images... 0%"
                : generatingBackgrounds
                  ? "Dang generate backgrounds... 0%"
                  : null
  );
  const currentActivityError = currentScenarioActivity?.status === "error"
    ? `${currentScenarioActivity.label}: ${currentScenarioActivity.error ?? "Failed"}`
    : null;
  const statusText = error ?? activeStatusText ?? currentActivityError ?? notice ?? (
    hasSceneEdits
      ? "Chua luu thay doi scene."
      : manualFlowHint
  );
  const statusClass = error || currentActivityError
    ? "scenario-dialog__error"
    : "scenario-dialog__notice";
  const footerProgressPercent = generatingSelected
    ? selectedGeneratePercent
    : exportingSelected
      ? selectedActivityPercent
      : currentScenarioActivityBusy
        ? currentScenarioActivityPercent
        : generatingAll
          ? generateAllPercent
          : 0;
  const showFooterProgress = (
    generatingSelected
    || exportingSelected
    || currentScenarioActivityBusy
    || generatingAll
    || generatingVideos
    || generatingImages
    || generatingBackgrounds
    || exportingScenario
  );

  return (
    <div
      className="scenario-dialog-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="scenario-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="scenario-dialog-title"
      >
        <div className="scenario-dialog__header">
          <div>
            <h2 id="scenario-dialog-title" className="scenario-dialog__title">
              Scenario Planner
            </h2>
            <p className="scenario-dialog__subtitle">
              {boardName || "Untitled"} · board #{boardId ?? "-"}
            </p>
          </div>
          <button
            type="button"
            className="scenario-dialog__close"
            onClick={onClose}
            aria-label="Close scenario planner"
            title="Close"
          >
            esc
          </button>
        </div>

        <div className="scenario-dialog__body">
          <div className="scenario-dialog__form">
            <section className="scenario-history">
              <div className="scenario-dialog__section-head">
                <div>
                  <h3 className="scenario-dialog__section-title">Saved scenarios</h3>
                  <p className="scenario-dialog__section-hint">
                    Open an earlier Auto Flow plan for this board.
                  </p>
                </div>
                <div className="scenario-history__actions">
                  <button
                    type="button"
                    className="scenario-history__refresh"
                    onClick={() => void handleNewScenario()}
                    disabled={!canStartNewScenario}
                  >
                    New
                  </button>
                  <button
                    type="button"
                    className="scenario-history__refresh"
                    onClick={() => void refreshScenarioHistory()}
                    disabled={loadingHistory}
                  >
                    {loadingHistory ? "Loading..." : "Refresh"}
                  </button>
                </div>
              </div>
              <input
                className="scenario-history__search"
                value={scenarioSearch}
                onChange={(e) => setScenarioSearch(e.target.value)}
                placeholder="Search scenarios"
                disabled={loadingHistory}
              />
              <div className="scenario-history__tools">
                <label className="scenario-history__filter">
                  <span>Stage</span>
                  <select
                    value={scenarioStageFilter}
                    onChange={(e) => (
                      setScenarioStageFilter(
                        e.target.value as "all" | ScenarioGenerationStage,
                      )
                    )}
                    disabled={loadingHistory}
                  >
                    {SCENARIO_STAGE_FILTERS.map(([stage, label]) => (
                      <option key={stage} value={stage}>{label}</option>
                    ))}
                  </select>
                </label>
                <div className="scenario-history__select-actions">
                  <button
                    type="button"
                    className="scenario-history__refresh"
                    onClick={selectVisibleScenarios}
                    disabled={filteredScenarioIds.length === 0 || selectedBatchBusy}
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    className="scenario-history__refresh"
                    onClick={clearScenarioSelection}
                    disabled={selectedScenarioIds.length === 0 || selectedBatchBusy}
                  >
                    Clear all
                  </button>
                  <span className="scenario-history__selected-count">
                    {selectedScenarioIds.length} selected
                    {filteredScenarioIds.length > 0
                      ? ` (${selectedVisibleCount}/${filteredScenarioIds.length} shown)`
                      : ""}
                  </span>
                </div>
              </div>
              {scenarioHistory.length === 0 ? (
                <div className="scenario-dialog__empty">
                  {loadingHistory ? "Loading saved scenarios..." : "No saved scenarios yet."}
                </div>
              ) : filteredScenarioHistory.length === 0 ? (
                <div className="scenario-dialog__empty">No matching scenarios.</div>
              ) : (
                <div className="scenario-history__list">
                  {filteredScenarioHistory.map((scenario) => {
                    const active = result?.scenario.id === scenario.id;
                    const opening = loadingScenarioId === scenario.id;
                    const deleting = deletingScenarioId === scenario.id;
                    const selected = selectedScenarioIds.includes(scenario.id);
                    const summary = scenario.generation_summary;
                    const runProgress = selectedScenarioProgress[scenario.id];
                    const scenarioActivity = scenarioActivities[scenario.id];
                    const scenarioActivityPercent = activityPercent(scenarioActivity);
                    const scenarioActivityBusy = isRunningActivity(scenarioActivity);
                    const deleteLocked = (
                      (active && currentScenarioBusy)
                      || runProgress?.status === "queued"
                      || runProgress?.status === "running"
                      || scenarioActivityBusy
                    );
                    return (
                      <div
                        key={scenario.id}
                        className={`scenario-history__item${
                          active ? " scenario-history__item--active" : ""
                        }${scenarioActivityBusy ? " scenario-history__item--busy" : ""}`}
                      >
                        <label className="scenario-history__check">
                          <input
                            type="checkbox"
                            checked={selected}
                            onChange={() => toggleScenarioSelection(scenario.id)}
                            disabled={selectedBatchBusy}
                            aria-label={`Select scenario ${scenario.id}`}
                          />
                        </label>
                        <button
                          type="button"
                          className="scenario-history__open"
                          onClick={() => void handleOpenSavedScenario(scenario.id)}
                          disabled={!canOpenHistoryScenario}
                        >
                          <span className="scenario-history__main">
                            <span className="scenario-history__title">
                              {scenario.theme || `Scenario #${scenario.id}`}
                            </span>
                            <span className="scenario-history__meta">
                              #{scenario.id} - {scenario.scene_count} scenes -{" "}
                              {videoAudioModeLabel(scenario.video_audio_mode)} - {scenario.status}
                            </span>
                            <span className="scenario-history__meta">
                              {scenarioStageLabel(scenarioStage(scenario))}
                              {summary
                                ? ` - bg ${summary.background_done}/${summary.scene_count}, img ${summary.image_done}/${summary.scene_count}, vid ${summary.video_done}/${summary.scene_count}`
                                : ""}
                            </span>
                            {runProgress && !scenarioActivity ? (
                              <span className={`scenario-history__run scenario-history__run--${runProgress.status}`}>
                                {runProgress.status}
                                {runProgress.label ? ` - ${runProgress.label}` : ""}
                                {runProgress.error ? ` - ${runProgress.error}` : ""}
                              </span>
                            ) : null}
                            {scenarioActivity ? (
                              <span className={`scenario-history__activity scenario-history__activity--${scenarioActivity.status}`}>
                                <span className="scenario-history__activity-head">
                                  <span>
                                    {scenarioActivity.status} - {scenarioActivity.label}
                                    {scenarioActivity.error ? ` - ${scenarioActivity.error}` : ""}
                                  </span>
                                  <strong>{scenarioActivityPercent}%</strong>
                                </span>
                                <span className="scenario-history__activity-bar">
                                  <span
                                    className="scenario-history__activity-fill"
                                    style={{ width: `${scenarioActivityPercent}%` }}
                                  />
                                </span>
                              </span>
                            ) : null}
                          </span>
                          <span className="scenario-history__side">
                            {opening ? "Opening..." : formatScenarioDate(scenario.updated_at)}
                          </span>
                        </button>
                        <button
                          type="button"
                          className="scenario-history__delete"
                          onClick={() => void handleDeleteSavedScenario(scenario)}
                          disabled={deletingScenarioId !== null || submitting || saving || deleteLocked}
                        >
                          {deleting ? "Deleting..." : "Delete"}
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            <section className="scenario-planner-mode">
              <button
                type="button"
                className={`scenario-planner-mode__option${
                  plannerMode === "manual" ? " scenario-planner-mode__option--active" : ""
                }`}
                onClick={() => setPlannerMode("manual")}
              >
                Manual
              </button>
              <button
                type="button"
                className={`scenario-planner-mode__option${
                  plannerMode === "batch" ? " scenario-planner-mode__option--active" : ""
                }`}
                onClick={() => setPlannerMode("batch")}
              >
                Batch from Folder
              </button>
            </section>

            {plannerMode === "manual" ? (
              <>
            <section className="scenario-video-audio">
              <div className="scenario-dialog__section-head">
                <div>
                  <h3 className="scenario-dialog__section-title">Video audio</h3>
                  <p className="scenario-dialog__section-hint">
                    Dialogue mode for generated videos.
                  </p>
                </div>
              </div>
              <div className="scenario-audio-mode-grid">
                {VIDEO_AUDIO_MODES.map(([mode, label, hint]) => (
                  <button
                    key={mode}
                    type="button"
                    className={`scenario-audio-mode${
                      videoAudioMode === mode ? " scenario-audio-mode--active" : ""
                    }`}
                    onClick={() => handleVideoAudioModeChange(mode)}
                    disabled={submitting || saving}
                  >
                    <span className="scenario-audio-mode__label">{label}</span>
                    <span className="scenario-audio-mode__hint">{hint}</span>
                  </button>
                ))}
              </div>
            </section>

            <section className={`scenario-voice-picker${
              voicePickerEnabled ? "" : " scenario-voice-picker--disabled"
            }`}>
              <div className="scenario-dialog__section-head">
                <div>
                  <h3 className="scenario-dialog__section-title">ElevenLabs voice</h3>
                  <p className="scenario-dialog__section-hint">
                    {voicePickerEnabled
                      ? "Saved on this Scenario and used during export."
                      : "Only used by Veo + ElevenLabs mode."}
                  </p>
                </div>
                <button
                  type="button"
                  className="scenario-history__refresh"
                  onClick={() => void refreshVoices()}
                  disabled={loadingVoices || !voicePickerEnabled}
                >
                  {loadingVoices ? "Loading..." : "Voices"}
                </button>
              </div>
              <div className="scenario-voice-picker__grid">
                <label className="scenario-dialog__field">
                  <span className="scenario-dialog__label">Voice</span>
                  <select
                    className="scenario-dialog__select"
                    value={selectedVoiceId}
                    onChange={(e) => handleVoiceIdChange(e.target.value)}
                    disabled={!voicePickerEnabled || voices.length === 0 || submitting || saving}
                  >
                    {voices.length === 0 ? (
                      <option value="">No voices loaded</option>
                    ) : (
                      <>
                        <option value="">Choose a voice</option>
                        {voices.map((voice) => (
                          <option key={voice.voice_id} value={voice.voice_id}>
                            {voice.name} ({voice.voice_id})
                          </option>
                        ))}
                      </>
                    )}
                  </select>
                </label>
              </div>
            </section>

            <div className="scenario-dialog__field">
              <label className="scenario-dialog__label" htmlFor="scenario-theme">
                Chủ đề kịch bản
              </label>
              <input
                ref={firstInputRef}
                id="scenario-theme"
                className="scenario-dialog__input"
                value={theme}
                onChange={(e) => setTheme(e.target.value)}
                placeholder="Ví dụ: launch áo thun premium cho Gen Z tại Seoul"
                maxLength={180}
              />
            </div>

            <div className="scenario-dialog__field">
              <label className="scenario-dialog__label" htmlFor="scenario-extra">
                Mô tả bổ sung
              </label>
              <textarea
                id="scenario-extra"
                className="scenario-dialog__textarea"
                value={extraDescription}
                onChange={(e) => setExtraDescription(e.target.value)}
                placeholder="Mood, thông điệp, ngôn ngữ voice, yêu cầu sản phẩm, CTA..."
                rows={4}
                maxLength={700}
              />
            </div>

            <div className="scenario-dialog__split">
              <div className="scenario-dialog__field">
                <span className="scenario-dialog__label">Số lượng cảnh</span>
                <div className="scenario-stepper">
                  <button
                    type="button"
                    disabled={sceneCount <= 1}
                    onClick={() => setSceneCount((v) => Math.max(1, v - 1))}
                    aria-label="Decrease scene count"
                  >
                    -
                  </button>
                  <input
                    className="scenario-stepper__input"
                    type="number"
                    min={1}
                    max={12}
                    value={sceneCount}
                    onChange={(e) => {
                      const next = Number.parseInt(e.target.value, 10);
                      setSceneCount(Number.isFinite(next) ? Math.max(1, Math.min(12, next)) : 1);
                    }}
                  />
                  <button
                    type="button"
                    disabled={sceneCount >= 12}
                    onClick={() => setSceneCount((v) => Math.min(12, v + 1))}
                    aria-label="Increase scene count"
                  >
                    +
                  </button>
                </div>
              </div>

              <div className="scenario-dialog__field">
                <span className="scenario-dialog__label">Phong cách nội dung</span>
                <select
                  id="scenario-content-style"
                  className="scenario-dialog__select"
                  value={contentStyle}
                  onChange={(e) => setContentStyle(asContentStyle(e.target.value))}
                  disabled={submitting || saving}
                >
                  {CONTENT_STYLES.map((style) => (
                    <option key={style} value={style}>{style}</option>
                  ))}
                </select>
              </div>
            </div>

            <RefPicker
              title="Background refs"
              hint="Image nodes seeding scene backgrounds."
              empty="Chưa có image node nào có mediaId để dùng làm background."
              candidates={backgroundCandidates}
              selected={backgroundIds}
              onToggle={(id) => setBackgroundIds((prev) => toggleId(prev, id))}
            />

            <RefPicker
              title="Character refs"
              hint="Character nodes the planner must keep consistent."
              empty="Chưa có character node nào đã render."
              candidates={characterCandidates}
              selected={characterIds}
              onToggle={(id) => setCharacterIds((prev) => toggleId(prev, id))}
            />

            <RefPicker
              title="Visual asset refs"
              hint="Products, wardrobe, props, or saved references."
              empty="Chưa có visual asset node nào đã render."
              candidates={assetCandidates}
              selected={assetIds}
              onToggle={(id) => setAssetIds((prev) => toggleId(prev, id))}
            />
              </>
            ) : (
              <section className="scenario-batch">
                <div className="scenario-dialog__section-head">
                  <div>
                    <h3 className="scenario-dialog__section-title">Batch from Folder</h3>
                    <p className="scenario-dialog__section-hint">
                      Each image or video file will become one Scenario.
                    </p>
                  </div>
                  <button
                    type="button"
                    className="scenario-history__refresh"
                    onClick={() => void refreshVoices()}
                    disabled={loadingVoices}
                  >
                    {loadingVoices ? "Loading..." : "Voices"}
                  </button>
                </div>

                <label className="scenario-dialog__field">
                  <span className="scenario-dialog__label">Folder path</span>
                  <input
                    className="scenario-dialog__input"
                    value={batchFolderPath}
                    onChange={(e) => setBatchFolderPath(e.target.value)}
                    placeholder="C:\\path\\to\\images-or-videos"
                  />
                </label>

                <div className="scenario-dialog__split">
                  <label className="scenario-dialog__field">
                    <span className="scenario-dialog__label">Input type</span>
                    <select
                      className="scenario-dialog__select"
                      value={batchInputType}
                      onChange={(e) => setBatchInputType(e.target.value as BatchInputType)}
                    >
                      <option value="auto">Auto detect</option>
                      <option value="images">Images only</option>
                      <option value="videos">Videos only</option>
                    </select>
                  </label>

                  <div className="scenario-dialog__field">
                    <span className="scenario-dialog__label">Scenes per Scenario</span>
                    <div className="scenario-stepper">
                      <button
                        type="button"
                        disabled={batchSceneCount <= 1}
                        onClick={() => setBatchSceneCount((v) => Math.max(1, v - 1))}
                        aria-label="Decrease batch scene count"
                      >
                        -
                      </button>
                      <input
                        className="scenario-stepper__input"
                        type="number"
                        min={1}
                        max={12}
                        value={batchSceneCount}
                        onChange={(e) => {
                          const next = Number.parseInt(e.target.value, 10);
                          setBatchSceneCount(
                            Number.isFinite(next) ? Math.max(1, Math.min(12, next)) : 1,
                          );
                        }}
                      />
                      <button
                        type="button"
                        disabled={batchSceneCount >= 12}
                        onClick={() => setBatchSceneCount((v) => Math.min(12, v + 1))}
                        aria-label="Increase batch scene count"
                      >
                        +
                      </button>
                    </div>
                  </div>
                </div>

                <label className="scenario-dialog__field">
                  <span className="scenario-dialog__label">Voice</span>
                  <select
                    className="scenario-dialog__select"
                    value={batchVoiceId}
                    onChange={(e) => setBatchVoiceId(e.target.value)}
                    disabled={voices.length === 0 || loadingVoices}
                  >
                    {voices.length === 0 ? (
                      <option value="">No voices loaded</option>
                    ) : (
                      <>
                        <option value="">Choose a voice</option>
                        {voices.map((voice) => (
                          <option key={voice.voice_id} value={voice.voice_id}>
                            {voice.name} ({voice.voice_id})
                          </option>
                        ))}
                      </>
                    )}
                  </select>
                </label>

                <div className="scenario-batch__actions">
                  <button
                    type="button"
                    className="scenario-dialog__secondary"
                    onClick={() => { void handleBatchScan(); }}
                    disabled={batchFolderPath.trim().length === 0}
                    title={batchFolderPath.trim() ? "" : "Enter a folder path first."}
                  >
                    Scan folder
                  </button>
                  <button
                    type="button"
                    className="scenario-dialog__secondary scenario-dialog__secondary--strong"
                    onClick={() => { void handleBatchGenerate(); }}
                    disabled={batchFiles.length === 0 || !batchVoiceId}
                    title={
                      batchFiles.length === 0
                        ? "Scan a folder first."
                        : !batchVoiceId
                          ? "Choose a voice first."
                          : ""
                    }
                  >
                    Generate Scenarios
                  </button>
                </div>

                <div className="scenario-batch__list">
                  {batchFiles.length === 0 ? (
                    <div className="scenario-dialog__empty">
                      No files scanned yet. Task 16 will connect the folder scanner.
                    </div>
                  ) : (
                    batchFiles.map((file) => (
                      <div key={file.id} className="scenario-batch-file">
                        <div className="scenario-batch-file__head">
                          <span>{file.name}</span>
                          <strong>{file.progress}%</strong>
                        </div>
                        <div className="scenario-batch-file__meta">
                          {file.kind} - {file.status}
                          {file.message ? ` - ${file.message}` : ""}
                        </div>
                        <div className="scenario-batch-file__bar">
                          <span style={{ width: `${file.progress}%` }} />
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </section>
            )}
          </div>

          <aside className="scenario-dialog__preview">
            <h3 className="scenario-dialog__preview-title">Planner result</h3>
            <p className="scenario-dialog__preview-copy">
              Review và chỉnh từng scene trước khi chạy bước generate.
            </p>
            {result ? (
              <>
                <div className="scenario-dialog__result-meta">
                  Scenario #{result.scenario.id} · {result.scenes.length} scenes
                </div>
                {currentScenarioActivity ? (
                  <div className={`scenario-dialog__result-activity scenario-dialog__result-activity--${currentScenarioActivity.status}`}>
                    <div className="scenario-dialog__result-activity-head">
                      <span>
                        {currentScenarioActivity.status} - {currentScenarioActivity.label}
                      </span>
                      <strong>{currentScenarioActivityPercent}%</strong>
                    </div>
                    <div className="scenario-dialog__result-activity-bar">
                      <span
                        className="scenario-dialog__result-activity-fill"
                        style={{ width: `${currentScenarioActivityPercent}%` }}
                      />
                    </div>
                    {currentScenarioActivity.error ? (
                      <div className="scenario-dialog__result-activity-error">
                        {currentScenarioActivity.error}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                <ScenePlanEditor
                  scenes={sceneDrafts}
                  backgroundProgress={backgroundProgress}
                  imageProgress={imageProgress}
                  videoProgress={videoProgress}
                  onSceneChange={handleSceneDraftChange}
                />
                {result.scenario.final_video_media_id ? (
                  <div className="scenario-export-preview">
                    <div className="scenario-scene-media__label">Exported scenario</div>
                    <video
                      className="scenario-export-preview__video"
                      src={mediaUrl(result.scenario.final_video_media_id)}
                      controls
                      playsInline
                    />
                    <a
                      className="scenario-export-preview__download"
                      href={mediaUrl(result.scenario.final_video_media_id)}
                      download
                    >
                      Download final video
                    </a>
                  </div>
                ) : null}
              </>
            ) : preview ? (
              <pre className="scenario-dialog__payload">
                {JSON.stringify(preview, null, 2)}
              </pre>
            ) : (
              <div className="scenario-dialog__placeholder">
                Điền form và bấm Generate Script để xem payload review.
              </div>
            )}
          </aside>
        </div>

        <div className="scenario-dialog__footer">
          <div className="scenario-dialog__status" role={error ? "alert" : "status"}>
            <span className={statusClass}>{statusText}</span>
            {showFooterProgress ? (
              <div className="scenario-generate-all-progress" aria-label="Generation progress">
                <span
                  className="scenario-generate-all-progress__bar"
                  style={{ width: `${footerProgressPercent}%` }}
                />
              </div>
            ) : null}
          </div>
          <div className="scenario-dialog__footer-actions">
            <button
              type="button"
              className="scenario-dialog__secondary scenario-dialog__secondary--strong"
              onClick={handleGenerateSelected}
              disabled={!canGenerateSelected}
              title={generateSelectedDisabledReason}
            >
              {generatingSelected
                ? `Generate selected ${selectedGeneratePercent}%`
                : "Generate selected"}
            </button>
            <button
              type="button"
              className="scenario-dialog__secondary"
              onClick={handleExportSelectedScenarios}
              disabled={!canExportSelected}
              title={exportSelectedDisabledReason}
            >
              {exportingSelected ? "Exporting selected..." : "Export selected"}
            </button>
            {plannerMode === "manual" && result && (
              <>
                <button
                  type="button"
                  className="scenario-dialog__secondary scenario-dialog__secondary--strong"
                  onClick={handleGenerateAll}
                  disabled={!canGenerateAll}
                  title={generateAllDisabledReason}
                >
                  {generatingAll ? `Generate all ${generateAllPercent}%` : "Generate all"}
                </button>
                <button
                  type="button"
                  className="scenario-dialog__secondary"
                  onClick={handleSaveSceneDrafts}
                  disabled={saving || submitting || !hasSceneEdits}
                >
                  {saving ? "Saving..." : "Save edits"}
                </button>
                <button
                  type="button"
                  className="scenario-dialog__secondary"
                  onClick={handleGenerateBackgrounds}
                  disabled={!canGenerateBackgrounds}
                >
                  {generatingBackgrounds ? "Generating backgrounds..." : "Generate backgrounds"}
                </button>
                <button
                  type="button"
                  className="scenario-dialog__secondary"
                  onClick={handleGenerateSceneImages}
                  disabled={!canGenerateSceneImages}
                  title={sceneImagesDisabledReason}
                >
                  {generatingImages ? "Generating scene images..." : "Generate scene images"}
                </button>
                <button
                  type="button"
                  className="scenario-dialog__secondary"
                  onClick={handleGenerateVideos}
                  disabled={!canGenerateVideos}
                  title={videosDisabledReason}
                >
                  {generatingVideos ? "Generating videos..." : "Generate videos"}
                </button>
                <button
                  type="button"
                  className="scenario-dialog__secondary"
                  onClick={handleExportScenario}
                  disabled={!canExportScenario}
                  title={exportScenarioDisabledReason}
                >
                  {exportingScenario ? "Exporting..." : "Export scenario"}
                </button>
              </>
            )}
            {plannerMode === "manual" ? (
              <button
                type="button"
                className="scenario-dialog__cta"
                onClick={handleGenerateScript}
                disabled={!canSubmitScript}
                title={submitScriptDisabledReason}
              >
                {submitting
                  ? "Generating..."
                  : result
                    ? "Regenerate Script"
                    : "Generate Script"}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
