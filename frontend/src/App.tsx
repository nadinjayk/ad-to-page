import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";

type ViewportPreset = "desktop" | "tablet" | "mobile";
type ColorStrategy = "preserve_site" | "use_ad";
type AssetPreference = "placeholders" | "searched";
type DownloadTarget = "output" | null;

const LAST_JOB_KEY = "design-transfer-last-job-id";
const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8765";

const VIEWPORTS: Record<ViewportPreset, { width: number; height: number; label: string }> = {
  desktop: { width: 1440, height: 900, label: "Desktop 1440x900" },
  tablet: { width: 1024, height: 768, label: "Tablet 1024x768" },
  mobile: { width: 390, height: 844, label: "Mobile 390x844" }
};

type ScrapeResponse = {
  job_id: string;
  status: string;
  screenshot_url: string;
  dom_url: string;
  styles_url: string;
  title: string;
  visible_element_count: number;
  viewport: {
    width: number;
    height: number;
  };
};

type ReconstructResponse = {
  job_id: string;
  status: string;
  html_url: string;
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
};

type BrandExtractResponse = {
  job_id: string;
  status: string;
  ad_image_url: string;
  brand_identity: Record<string, unknown>;
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
};

type ReskinResponse = {
  job_id: string;
  status: string;
  html_url: string;
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
};

type JobDetailsResponse = {
  id: string;
  url: string;
  viewport: {
    width: number;
    height: number;
  };
  status: string;
  screenshot_path: string | null;
  dom_data: {
    title?: string;
    visibleElements?: Array<unknown>;
  } | null;
  reconstruction_html_path: string | null;
  ad_image_path: string | null;
  brand_identity: Record<string, unknown> | null;
  reskinned_html_path: string | null;
};

function slugify(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "design-transfer";
}

function FittedViewport({
  width,
  height,
  children
}: {
  width: number;
  height: number;
  children: ReactNode;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) {
      return;
    }

    const updateScale = () => {
      const bounds = element.getBoundingClientRect();
      const nextScale = Math.min(bounds.width / width, bounds.height / height);
      setScale(Number.isFinite(nextScale) && nextScale > 0 ? nextScale : 1);
    };

    updateScale();

    const observer = new ResizeObserver(() => updateScale());
    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [height, width]);

  return (
    <div ref={containerRef} className="fitted-viewport">
      <div
        className="fitted-viewport-frame"
        style={{
          width: `${width * scale}px`,
          height: `${height * scale}px`
        }}
      >
        <div
          className="fitted-viewport-inner"
          style={{
            width: `${width}px`,
            height: `${height}px`,
            transform: `scale(${scale})`
          }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [url, setUrl] = useState("");
  const [preset, setPreset] = useState<ViewportPreset>("desktop");
  const [result, setResult] = useState<ScrapeResponse | null>(null);
  const [reconstruction, setReconstruction] = useState<ReconstructResponse | null>(null);
  const [brandExtraction, setBrandExtraction] = useState<BrandExtractResponse | null>(null);
  const [reskin, setReskin] = useState<ReskinResponse | null>(null);
  const [brandIdentity, setBrandIdentity] = useState<Record<string, unknown> | null>(null);
  const [selectedAdFile, setSelectedAdFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reconstructError, setReconstructError] = useState<string | null>(null);
  const [brandError, setBrandError] = useState<string | null>(null);
  const [reskinError, setReskinError] = useState<string | null>(null);
  const [backendReady, setBackendReady] = useState(false);
  const [isRestoring, setIsRestoring] = useState(false);
  const [isProcessingUrl, setIsProcessingUrl] = useState(false);
  const [isProcessingAd, setIsProcessingAd] = useState(false);
  const [isConverting, setIsConverting] = useState(false);
  const [isDraggingAd, setIsDraggingAd] = useState(false);
  const [showConvertModal, setShowConvertModal] = useState(false);
  const [colorStrategy, setColorStrategy] = useState<ColorStrategy>("use_ad");
  const [assetPreference, setAssetPreference] = useState<AssetPreference>("searched");
  const [downloadTarget, setDownloadTarget] = useState<DownloadTarget>(null);

  const viewport = useMemo(() => VIEWPORTS[preset], [preset]);
  const previewViewport = result?.viewport || viewport;
  const canConvert =
    backendReady && Boolean(result && reconstruction && brandIdentity) && !isConverting;
  const projectLabel = result?.title || "Output";

  function clearMessages() {
    setError(null);
    setReconstructError(null);
    setBrandError(null);
    setReskinError(null);
  }

  function resetSession() {
    window.localStorage.removeItem(LAST_JOB_KEY);
    setUrl("");
    setPreset("desktop");
    setResult(null);
    setReconstruction(null);
    setBrandExtraction(null);
    setReskin(null);
    setBrandIdentity(null);
    setSelectedAdFile(null);
    setShowConvertModal(false);
    clearMessages();
  }

  function setJobState(job: JobDetailsResponse) {
    setUrl(job.url);

    if (job.screenshot_path) {
      setResult({
        job_id: job.id,
        status: job.status,
        screenshot_url: `/files/${job.id}/screenshot.png`,
        dom_url: `/files/${job.id}/dom.json`,
        styles_url: `/files/${job.id}/styles.json`,
        title: job.dom_data?.title || "Untitled",
        visible_element_count: job.dom_data?.visibleElements?.length || 0,
        viewport: job.viewport
      });
    } else {
      setResult(null);
    }

    if (job.reconstruction_html_path) {
      setReconstruction({
        job_id: job.id,
        status: job.status,
        html_url: `/files/${job.id}/reconstruction.html`,
        model: "",
        input_tokens: null,
        output_tokens: null
      });
    } else {
      setReconstruction(null);
    }

    if (job.brand_identity && job.ad_image_path) {
      const adImageName = job.ad_image_path.split(/[\\/]/).pop() || "ad-image.png";
      setBrandExtraction({
        job_id: job.id,
        status: job.status,
        ad_image_url: `/files/${job.id}/${adImageName}`,
        brand_identity: job.brand_identity,
        model: "",
        input_tokens: null,
        output_tokens: null
      });
      setBrandIdentity(job.brand_identity);
    } else {
      setBrandExtraction(null);
      setBrandIdentity(null);
    }

    if (job.reskinned_html_path) {
      setReskin({
        job_id: job.id,
        status: job.status,
        html_url: `/files/${job.id}/reskinned.html`,
        model: "",
        input_tokens: null,
        output_tokens: null
      });
    } else {
      setReskin(null);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function checkBackend() {
      try {
        const response = await fetch(`${API_BASE}/api/health`);
        if (!response.ok) {
          throw new Error("Backend health check failed.");
        }
        if (!cancelled) {
          setBackendReady(true);
        }
      } catch {
        if (!cancelled) {
          setBackendReady(false);
        }
      }
    }

    checkBackend();
    const interval = window.setInterval(checkBackend, 2500);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (!backendReady) {
      return;
    }

    const lastJobId = window.localStorage.getItem(LAST_JOB_KEY);
    if (!lastJobId) {
      return;
    }

    let cancelled = false;

    async function restoreJob() {
      setIsRestoring(true);
      try {
        const response = await fetch(`${API_BASE}/api/jobs/${lastJobId}`);
        if (response.status === 404) {
          window.localStorage.removeItem(LAST_JOB_KEY);
          return;
        }
        if (!response.ok) {
          throw new Error("Could not restore the last job.");
        }

        const data: JobDetailsResponse = await response.json();
        if (!cancelled) {
          setJobState(data);
        }
      } catch {
        if (!cancelled) {
          setError("Could not restore the last job.");
        }
      } finally {
        if (!cancelled) {
          setIsRestoring(false);
        }
      }
    }

    restoreJob();

    return () => {
      cancelled = true;
    };
  }, [backendReady]);

  useEffect(() => {
    if (!showConvertModal) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowConvertModal(false);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [showConvertModal]);

  async function processUrl(event?: FormEvent) {
    event?.preventDefault();

    if (!backendReady) {
      setError("Backend is still starting.");
      return;
    }

    setIsProcessingUrl(true);
    clearMessages();
    setResult(null);
    setReconstruction(null);
    setReskin(null);

    try {
      const scrapeResponse = await fetch(`${API_BASE}/api/scrape`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          url,
          viewport_width: viewport.width,
          viewport_height: viewport.height
        })
      });

      if (!scrapeResponse.ok) {
        const data = await scrapeResponse.json().catch(() => null);
        throw new Error(data?.detail || "URL processing failed.");
      }

      const scrapeData: ScrapeResponse = await scrapeResponse.json();
      setResult(scrapeData);
      window.localStorage.setItem(LAST_JOB_KEY, scrapeData.job_id);

      const reconstructResponse = await fetch(`${API_BASE}/api/reconstruct`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          job_id: scrapeData.job_id
        })
      });

      if (!reconstructResponse.ok) {
        const data = await reconstructResponse.json().catch(() => null);
        throw new Error(data?.detail || "Reconstruction failed.");
      }

      const reconstructData: ReconstructResponse = await reconstructResponse.json();
      setReconstruction(reconstructData);
      window.localStorage.setItem(LAST_JOB_KEY, reconstructData.job_id);
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : "URL processing failed.";
      setError(message);
    } finally {
      setIsProcessingUrl(false);
    }
  }

  function handleAdSelection(file: File | null) {
    setSelectedAdFile(file);
    setBrandError(null);
  }

  async function processAd() {
    if (!result || !selectedAdFile) {
      setBrandError("Drop an ad image first.");
      return;
    }

    setIsProcessingAd(true);
    setBrandError(null);
    setReskin(null);

    try {
      const formData = new FormData();
      formData.append("job_id", result.job_id);
      formData.append("ad_image", selectedAdFile);

      const response = await fetch(`${API_BASE}/api/extract-brand`, {
        method: "POST",
        body: formData
      });

      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || "Ad processing failed.");
      }

      const data: BrandExtractResponse = await response.json();
      setBrandExtraction(data);
      setBrandIdentity(data.brand_identity);
      window.localStorage.setItem(LAST_JOB_KEY, data.job_id);
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "Ad processing failed.";
      setBrandError(message);
    } finally {
      setIsProcessingAd(false);
    }
  }

  function openConvertModal() {
    if (!result || !reconstruction) {
      setReskinError("Process the URL first.");
      return;
    }

    if (!brandIdentity) {
      setReskinError("Process the ad first.");
      return;
    }

    setReskinError(null);
    setShowConvertModal(true);
  }

  async function performConvert() {
    if (!result || !reconstruction || !brandIdentity) {
      setReskinError("Complete the URL and ad steps first.");
      setShowConvertModal(false);
      return;
    }

    setShowConvertModal(false);
    setIsConverting(true);
    setReskinError(null);
    setReskin(null);

    try {
      const response = await fetch(`${API_BASE}/api/reskin`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          job_id: result.job_id,
          brand_identity: brandIdentity,
          asset_mode: assetPreference === "searched" ? "official_web" : "off",
          color_strategy: colorStrategy
        })
      });

      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || "Conversion failed.");
      }

      const data: ReskinResponse = await response.json();
      setReskin(data);
      window.localStorage.setItem(LAST_JOB_KEY, data.job_id);
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "Conversion failed.";
      setReskinError(message);
    } finally {
      setIsConverting(false);
    }
  }

  async function downloadHtml(htmlUrl: string, kind: Exclude<DownloadTarget, null>) {
    const filename = `${slugify(projectLabel)}-${kind}.html`;
    setDownloadTarget(kind);

    try {
      const response = await fetch(`${API_BASE}${htmlUrl}`);
      if (!response.ok) {
        throw new Error("Could not download the HTML file.");
      }

      const blob = await response.blob();
      const objectUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(objectUrl);
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "Download failed.";
      setReskinError(message);
    } finally {
      setDownloadTarget(null);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-name">Design Transfer</span>
          <span className="brand-subtle">{projectLabel}</span>
        </div>
        <div className={`live-tick ${backendReady ? "online" : ""}`}>{backendReady ? "✓" : ""}</div>
      </header>

      <section className="workspace">
        <aside className="sidebar">
          <form className="sidebar-panel" onSubmit={processUrl}>
            <div className="control-block">
              <label className="control-label" htmlFor="url-input">
                URL
              </label>
              <input
                id="url-input"
                className="control-input"
                type="url"
                placeholder="https://example.com"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                required
              />
              <select
                className="control-input"
                value={preset}
                onChange={(event) => setPreset(event.target.value as ViewportPreset)}
              >
                {Object.entries(VIEWPORTS).map(([key, value]) => (
                  <option key={key} value={key}>
                    {value.label}
                  </option>
                ))}
              </select>
              <button className="action-button primary" type="submit" disabled={!backendReady || isProcessingUrl}>
                {isProcessingUrl ? "Processing..." : "Process URL"}
              </button>
            </div>

            <div className="control-block">
              <label className="control-label">Ad Upload</label>
              <label
                className={`upload-area ${isDraggingAd ? "dragging" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  setIsDraggingAd(true);
                }}
                onDragLeave={(event) => {
                  event.preventDefault();
                  setIsDraggingAd(false);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setIsDraggingAd(false);
                  handleAdSelection(event.dataTransfer.files?.[0] || null);
                }}
              >
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(event) => handleAdSelection(event.target.files?.[0] || null)}
                />
                <span className="upload-title">
                  {selectedAdFile ? selectedAdFile.name : "Drop image here or click to upload"}
                </span>
                <span className="upload-subtle">
                  {brandExtraction ? "Ad analyzed and ready to convert." : "PNG, JPG, or WEBP"}
                </span>
              </label>
              <button
                className="action-button"
                type="button"
                onClick={processAd}
                disabled={!result || !selectedAdFile || isProcessingAd}
              >
                {isProcessingAd ? "Processing..." : "Process Ad"}
              </button>
            </div>

            <div className="sidebar-spacer" />

            {reskin && (
              <div className="control-block">
                <label className="control-label">Downloads</label>
                <button
                  className="action-button primary"
                  type="button"
                  onClick={() => downloadHtml(reskin.html_url, "output")}
                  disabled={downloadTarget !== null}
                >
                  {downloadTarget === "output" ? "Downloading..." : "Download Output HTML"}
                </button>
              </div>
            )}

            <div className="action-stack">
              <button className="action-button primary" type="button" onClick={openConvertModal} disabled={!canConvert}>
                {isConverting ? "Converting..." : "Convert"}
              </button>

              <button className="action-button" type="button" onClick={resetSession}>
                Reset Session
              </button>
            </div>

            {(error || reconstructError || brandError || reskinError || isRestoring) && (
              <div className="error-stack">
                {isRestoring && <div className="error-box muted">Restoring the last session...</div>}
                {[error, reconstructError, brandError, reskinError]
                  .filter(Boolean)
                  .map((message) => (
                    <div key={message} className="error-box">
                      {message}
                    </div>
                  ))}
              </div>
            )}
          </form>
        </aside>

        <section className="display-stage">
          {!result ? (
            <div className="display-empty-shell">
              <div className="empty-canvas">
                <h1>Awaiting Input</h1>
              </div>
            </div>
          ) : (
            <div className="display-stack single">
              <div className="viewer-card single">
                <div className="viewer-head">
                  <span>{reskin ? "Converted" : "Source"}</span>
                </div>

                <div className="preview-stage">
                  {reskin ? (
                    <FittedViewport width={previewViewport.width} height={previewViewport.height}>
                      <iframe
                        src={`${API_BASE}${reskin.html_url}`}
                        title="Converted branded webpage"
                        scrolling="no"
                      />
                    </FittedViewport>
                  ) : (
                    <div className="source-preview-stage">
                      <img
                        className="source-preview-image"
                        src={`${API_BASE}${result.screenshot_url}`}
                        alt={`Source screenshot for ${result.title}`}
                      />
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </section>
      </section>

      {showConvertModal && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setShowConvertModal(false);
            }
          }}
        >
          <div className="modal-shell" role="dialog" aria-modal="true" aria-labelledby="convert-title">
            <div className="modal-header">
              <div>
                <div className="control-label">Convert</div>
                <h2 id="convert-title">Choose Output Settings</h2>
              </div>
              <button className="modal-close" type="button" onClick={() => setShowConvertModal(false)}>
                ×
              </button>
            </div>

            <div className="modal-group">
              <span className="modal-label">Color Scheme</span>
              <div className="modal-options">
                <button
                  className={`option-card ${colorStrategy === "preserve_site" ? "active" : ""}`}
                  type="button"
                  onClick={() => setColorStrategy("preserve_site")}
                >
                  <span className="option-title">Preserve Site Color Scheme</span>
                  <span className="option-copy">Keep the original page palette and only transfer the ad branding into copy and styling direction.</span>
                </button>
                <button
                  className={`option-card ${colorStrategy === "use_ad" ? "active" : ""}`}
                  type="button"
                  onClick={() => setColorStrategy("use_ad")}
                >
                  <span className="option-title">Use Ad Color Scheme</span>
                  <span className="option-copy">Apply the palette extracted from the ad analysis across the converted page.</span>
                </button>
              </div>
            </div>

            <div className="modal-group">
              <span className="modal-label">Assets</span>
              <div className="modal-options">
                <button
                  className={`option-card ${assetPreference === "placeholders" ? "active" : ""}`}
                  type="button"
                  onClick={() => setAssetPreference("placeholders")}
                >
                  <span className="option-title">Use Placeholder Assets</span>
                  <span className="option-copy">Skip image search entirely and let the layout render with abstract or generated placeholders.</span>
                </button>
                <button
                  className={`option-card ${assetPreference === "searched" ? "active" : ""}`}
                  type="button"
                  onClick={() => setAssetPreference("searched")}
                >
                  <span className="option-title">Use Searched Assets</span>
                  <span className="option-copy">Attempt the tightened brand-plus-product asset lookup before falling back to placeholders.</span>
                </button>
              </div>
            </div>

            <div className="modal-actions">
              <button className="action-button" type="button" onClick={() => setShowConvertModal(false)}>
                Cancel
              </button>
              <button className="action-button primary" type="button" onClick={performConvert}>
                Start Convert
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
