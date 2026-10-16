"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  interface Window {
    DocsAPI?: { DocEditor: new (id: string, config: any) => any };
  }
}

// The Document Server's editor bootstrap. Loaded once per DS origin, then reused.
const SCRIPT_PATH = "/web-apps/apps/api/documents/api.js";
const loaders: Record<string, Promise<void> | undefined> = {};

function loadDocsApi(dsUrl: string): Promise<void> {
  const src = dsUrl.replace(/\/$/, "") + SCRIPT_PATH;
  if (window.DocsAPI) return Promise.resolve();
  const existing = loaders[src];
  if (existing) return existing;
  const pending = new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => {
      delete loaders[src];
      reject(new Error(`문서 서버에 연결하지 못했습니다 (${src})`));
    };
    document.head.appendChild(s);
  });
  loaders[src] = pending;
  return pending;
}

// Embeds the OnlyOffice editor for the translated .docx. The Document Server owns
// pagination + WYSIWYG and autosaves via its server-side callback, so there's no
// client-side save: the .docx on the backend is the source of truth.
export function OnlyOfficeEditor({
  docId,
  lang,
  onConnector,
  reloadKey,
  bookmark,
}: {
  docId: string;
  lang: string;
  onConnector?: (connector: any | null) => void;
  // Bump to force a re-init (re-fetch the freshly rendered .docx) — used to reflect an
  // applied re-translation in OnlyOffice Community (which has no connector).
  reloadKey?: number;
  // Open the editor scrolled to this block bookmark (e.g. "blk12") via actionLink.
  bookmark?: string | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const host = containerRef.current;
    if (!host) return;
    let cancelled = false;
    // DocEditor replaces its target node with an iframe. That target must not be a
    // React-owned element, or the next render throws removeChild (the node is gone).
    const placeholder = document.createElement("div");
    placeholder.style.height = "100%";
    placeholder.style.width = "100%";
    host.appendChild(placeholder);

    (async () => {
      try {
        const { document_server_url, config } = await api.editorConfig(docId, lang, bookmark);
        await loadDocsApi(document_server_url);
        if (cancelled || !placeholder.isConnected || !window.DocsAPI) return;

        const id = `onlyoffice-${docId}-${lang}`;
        placeholder.id = id;
        editorRef.current = new window.DocsAPI.DocEditor(id, {
          ...config,
          width: "100%",
          height: "100%",
          events: {
            onError: (e: any) => {
              const data = e?.data;
              const description =
                typeof data?.errorDescription === "string"
                  ? data.errorDescription.replace(/<br\s*\/?>/gi, " ")
                  : null;
              setError(
                typeof data === "string" ? data : description || "편집기 오류",
              );
            },
            onDocumentReady: () => {
              // createConnector exists only in OnlyOffice Developer Edition; in Community
              // it is undefined → connector stays null and apply falls back to re-render+reload.
              try {
                onConnector?.(editorRef.current?.createConnector?.() ?? null);
              } catch {
                onConnector?.(null);
              }
            },
          },
        });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();

    return () => {
      cancelled = true;
      onConnector?.(null);
      try {
        editorRef.current?.destroyEditor?.();
      } catch {
        /* editor may not have finished mounting */
      }
      editorRef.current = null;
      // Drop the iframe DocEditor swapped in so the React host stays an empty div.
      host.replaceChildren();
    };
  }, [docId, lang, reloadKey, bookmark]);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center">
        <p className="text-sm" style={{ color: "var(--danger)" }}>
          편집기 오류 — {error}
        </p>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      {/* Mask the OnlyOffice tab strip (#toolbar .box-tabs: 파일/홈/삽입…). It lives in a
          cross-origin iframe we can't restyle, and the DS's `toolbarNoTabs` customization
          is broken on this 9.4.0 build (it requests a missing index_loader.html → 404). So
          we cover the tab row from our side with an opaque strip matching the editor's light
          chrome; the formatting toolbar just below stays visible and usable. */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-0"
        style={{ height: "32px", background: "#f7f7f8", zIndex: 5 }}
      />
    </div>
  );
}
