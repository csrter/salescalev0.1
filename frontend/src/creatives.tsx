/**
 * Meta creative builder + placement-accurate previews. Previews come from
 * Meta's /previews edge, which renders the creative in the real placement
 * template (feed, story, right column, …) — not a generic mockup. Building a
 * creative can't change spend by itself; attaching it to an ad goes through
 * the staged-change flow like every other write.
 */

import { useCallback, useEffect, useState } from "react";
import { api, type CreativeRow } from "./api";

interface Page {
  id: string;
  name: string;
}

export function CreativesPanel({ adAccountId }: { adAccountId: string }) {
  const [creatives, setCreatives] = useState<CreativeRow[]>([]);
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
    <div className="subpanel">
      {error && <p className="error">{error}</p>}
      <table className="compact">
        <tbody>
          {creatives.map((c) => (
            <tr key={c.id}>
              <td>{c.name ?? c.external_id}</td>
              <td className="muted">{c.title ?? ""}</td>
              <td>
                <button className="link" onClick={() => setPreview(c)}>
                  Preview placements
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {creatives.length === 0 && <p className="muted">No creatives yet.</p>}
      <button onClick={() => setShowForm(!showForm)}>
        {showForm ? "Close" : "New creative"}
      </button>
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

  return (
    <div className="inline-form column">
      <input placeholder="Creative name" value={name} onChange={(e) => setName(e.target.value)} />
      <select value={pageId} onChange={(e) => setPageId(e.target.value)}>
        {pages.length === 0 && <option value="">No pages available</option>}
        {pages.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <textarea
        placeholder="Primary text"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
      />
      <input placeholder="Headline (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
      <input placeholder="Destination URL" value={link} onChange={(e) => setLink(e.target.value)} />
      <label>
        Image:{" "}
        <input
          type="file"
          accept="image/*"
          onChange={(e) => e.target.files?.[0] && uploadImage(e.target.files[0])}
        />
        {imageHash && <span className="badge success">uploaded</span>}
      </label>
      {error && <p className="error">{error}</p>}
      <button disabled={busy || !name || !pageId || !message || !link} onClick={create}>
        {busy ? "Working…" : "Create creative"}
      </button>
    </div>
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
    <div className="modal-backdrop">
      <div className="modal wide">
        <h3>Placement preview — {creative.name ?? creative.external_id}</h3>
        <div className="toggle">
          {formats.map((f) => (
            <button
              key={f}
              className={format === f ? "active" : ""}
              onClick={() => setFormat(f)}
            >
              {f.replaceAll("_", " ").toLowerCase()}
            </button>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
        {!html && !error && <p className="muted">Rendering via Meta…</p>}
        {html && (
          // Meta returns its own sandboxed iframe snippet for the placement.
          <div className="preview-frame" dangerouslySetInnerHTML={{ __html: html }} />
        )}
        <div className="modal-actions">
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
