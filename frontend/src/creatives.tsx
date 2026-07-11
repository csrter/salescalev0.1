/**
 * Meta creative builder + placement-accurate previews. Previews come from
 * Meta's /previews edge, which renders the creative in the real placement
 * template (feed, story, right column, …) — not a generic mockup. Building a
 * creative can't change spend by itself; attaching it to an ad goes through
 * the staged-change flow like every other write.
 */

import { useCallback, useEffect, useState } from "react";
import { api, type CreativeRow } from "./api";
import { Dialog } from "./components/Dialog";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  Field,
  Segmented,
  Skeleton,
  SkeletonText,
} from "./components/ui";
import { Eye, Megaphone } from "./components/icons";
import "./styles/views/manage.css";

interface Page {
  id: string;
  name: string;
}

export function CreativesPanel({ adAccountId }: { adAccountId: string }) {
  const [creatives, setCreatives] = useState<CreativeRow[] | null>(null);
  const [pages, setPages] = useState<Page[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [preview, setPreview] = useState<CreativeRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<CreativeRow[]>(`/api/ad-accounts/${adAccountId}/creatives`)
      .then(setCreatives)
      .catch((e) => setError(e.message));
  }, [adAccountId]);
  useEffect(load, [load]);
  useEffect(() => {
    api<Page[]>(`/api/ad-accounts/${adAccountId}/pages`)
      .then(setPages)
      .catch(() => setPages([]));
  }, [adAccountId]);

  return (
    <div className="mg-panel">
      {error && <Alert tone="danger">{error}</Alert>}

      {creatives === null ? (
        <div className="mg-gallery" aria-hidden="true">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="card mg-creative">
              <Skeleton height="150px" />
              <div className="mg-creative-body">
                <Skeleton width="70%" />
                <Skeleton width="45%" height="0.8em" />
              </div>
            </div>
          ))}
        </div>
      ) : creatives.length === 0 ? (
        <EmptyState icon={<Megaphone />} title="No creatives yet">
          Build a creative to preview how it renders across Meta placements
          before attaching it to an ad.
        </EmptyState>
      ) : (
        <div className="mg-gallery">
          {creatives.map((c) => (
            <div key={c.id} className="card mg-creative">
              <div
                className={`mg-creative-thumb${
                  c.thumbnail_url ? "" : " mg-creative-thumb--empty"
                }`}
              >
                {c.thumbnail_url ? (
                  <img src={c.thumbnail_url} alt="" />
                ) : (
                  <Eye size={28} aria-hidden="true" />
                )}
                <div className="mg-creative-overlay">
                  <Button variant="primary" size="sm" onClick={() => setPreview(c)}>
                    Preview placements
                  </Button>
                </div>
              </div>
              <div className="mg-creative-body">
                <span className="mg-creative-name">{c.name ?? c.external_id}</span>
                {c.title && <span className="mg-creative-title">{c.title}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="mg-form-actions">
        <Button variant={showForm ? "ghost" : "default"} onClick={() => setShowForm(!showForm)}>
          {showForm ? "Close" : "New creative"}
        </Button>
      </div>

      {showForm && (
        <CreativeForm
          adAccountId={adAccountId}
          pages={pages}
          onCreated={() => {
            setShowForm(false);
            load();
          }}
        />
      )}
      {preview && (
        <PreviewModal creative={preview} onClose={() => setPreview(null)} />
      )}
    </div>
  );
}

function CreativeForm({
  adAccountId,
  pages,
  onCreated,
}: {
  adAccountId: string;
  pages: Page[];
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [pageId, setPageId] = useState(pages[0]?.id ?? "");
  const [message, setMessage] = useState("");
  const [title, setTitle] = useState("");
  const [link, setLink] = useState("");
  const [imageHash, setImageHash] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const uploadImage = async (file: File) => {
    setBusy(true);
    try {
      const b64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () =>
          resolve((reader.result as string).split(",")[1] ?? "");
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const resp = await api<{ image_hash: string | null }>(
        `/api/ad-accounts/${adAccountId}/images`,
        { method: "POST", body: JSON.stringify({ name: file.name, data_b64: b64 }) }
      );
      setImageHash(resp.image_hash);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/ad-accounts/${adAccountId}/creatives`, {
        method: "POST",
        body: JSON.stringify({
          name,
          page_id: pageId,
          message,
          title: title || null,
          link,
          image_hash: imageHash,
          call_to_action: "LEARN_MORE",
        }),
      });
      onCreated();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const ready = Boolean(name && pageId && message && link);

  return (
    <form
      className="card mg-form mg-form--column"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) create();
      }}
    >
      <Field label="Creative name">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Internal name" />
      </Field>
      <Field label="Page">
        <select value={pageId} onChange={(e) => setPageId(e.target.value)}>
          {pages.length === 0 && <option value="">No pages available</option>}
          {pages.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Primary text">
        <textarea value={message} onChange={(e) => setMessage(e.target.value)} />
      </Field>
      <Field label="Headline" optional>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Shown under the image" />
      </Field>
      <Field label="Destination URL">
        <input value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://…" />
      </Field>
      <Field label="Image" optional>
        <input
          type="file"
          accept="image/*"
          onChange={(e) => e.target.files?.[0] && uploadImage(e.target.files[0])}
        />
      </Field>
      {imageHash && <Badge tone="ok">Image uploaded</Badge>}
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="mg-form-actions">
        <Button type="submit" variant="primary" busy={busy} disabled={busy || !ready}>
          Create creative
        </Button>
      </div>
    </form>
  );
}

function PreviewModal({
  creative,
  onClose,
}: {
  creative: CreativeRow;
  onClose: () => void;
}) {
  const [formats, setFormats] = useState<string[]>([]);
  const [format, setFormat] = useState("MOBILE_FEED_STANDARD");
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<string[]>("/api/meta/preview-formats").then(setFormats).catch(() => {});
  }, []);
  useEffect(() => {
    setHtml(null);
    setError(null);
    api<{ body: string }[]>(
      `/api/creatives/${creative.id}/previews?ad_format=${format}`
    )
      .then((rows) => setHtml(rows[0]?.body ?? null))
      .catch((e) => setError(e.message));
  }, [creative.id, format]);

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title={`Placement preview — ${creative.name ?? creative.external_id}`}
      footer={
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      }
    >
      {formats.length > 0 && (
        <Segmented<string>
          ariaLabel="Placement format"
          options={formats.map((f) => ({
            value: f,
            label: f.replaceAll("_", " ").toLowerCase(),
          }))}
          value={format}
          onChange={setFormat}
        />
      )}
      {error && <Alert tone="danger">{error}</Alert>}
      {!html && !error && <SkeletonText lines={4} />}
      {html && (
        // Meta returns its own sandboxed iframe snippet for the placement.
        <div className="mg-preview-frame" dangerouslySetInnerHTML={{ __html: html }} />
      )}
    </Dialog>
  );
}
