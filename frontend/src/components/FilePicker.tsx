"use client";

/**
 * FilePicker — tokenized file-input affordance.
 *
 * Replaces the raw `<input type="file">` + browser "선택된 파일 없음" string
 * with a `.btn-secondary` trigger, a `.slug` placeholder, and a `.chip-soft`
 * chip with a remove button when a file is chosen.
 *
 * The underlying <input> is sr-only; the wrapping <label> makes it keyboard-
 * and pointer-accessible without any JS focus tricks.
 */

import React, { useId, useRef } from "react";

interface FilePickerSingleProps {
  /** "file" | "files" — defaults to "file" (single-select) */
  multiple?: false;
  accept?: string;
  onFile: (f: File | null) => void;
  file: File | null;
  /** Trigger button label — defaults to "파일 선택" */
  label?: string;
  /** Forwarded to the hidden <input> for a11y */
  id?: string;
  disabled?: boolean;
}

interface FilePickerMultiProps {
  multiple: true;
  accept?: string;
  onFile: (files: File[]) => void;
  file: File[];
  label?: string;
  id?: string;
  disabled?: boolean;
}

type FilePickerProps = FilePickerSingleProps | FilePickerMultiProps;

export function FilePicker(props: FilePickerProps) {
  const autoId = useId();
  const inputId = props.id ?? autoId;
  const inputRef = useRef<HTMLInputElement>(null);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (props.multiple) {
      props.onFile(Array.from(e.target.files ?? []));
    } else {
      props.onFile(e.target.files?.[0] ?? null);
    }
  }

  function handleRemove(e: React.MouseEvent) {
    e.preventDefault();
    if (props.multiple) {
      props.onFile([]);
    } else {
      props.onFile(null);
    }
    // Reset the underlying input so the same file can be re-picked
    if (inputRef.current) inputRef.current.value = "";
  }

  const hasFile = props.multiple ? props.file.length > 0 : props.file !== null;
  const fileLabel = props.multiple
    ? props.file.length === 1
      ? props.file[0].name
      : props.file.length > 1
        ? `${props.file.length}개 파일 선택됨`
        : null
    : props.file?.name ?? null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* Visible trigger — a <label> styled as .btn-secondary so it activates the hidden input */}
      <label
        htmlFor={inputId}
        className="btn-secondary cursor-pointer select-none"
        style={props.disabled ? { opacity: 0.45, cursor: "not-allowed", pointerEvents: "none" } : undefined}
      >
        {props.label ?? "파일 선택"}
        {/* sr-only native input — keyboard-accessible via the label's htmlFor */}
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={props.accept}
          multiple={props.multiple}
          disabled={props.disabled}
          className="sr-only"
          onChange={handleChange}
        />
      </label>

      {/* Status: chip when file chosen, slug placeholder when not */}
      {hasFile && fileLabel ? (
        <span className="chip-soft flex items-center gap-1.5 max-w-xs">
          <span className="truncate">{fileLabel}</span>
          <button
            type="button"
            aria-label="파일 제거"
            onClick={handleRemove}
            className="ml-0.5 flex-none rounded-full leading-none transition-opacity hover:opacity-60 focus-visible:outline-none"
            style={{ boxShadow: "none" }}
          >
            {/* ✕ using SVG for consistent sizing */}
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" fill="currentColor">
              <path d="M1.5 1.5l7 7M8.5 1.5l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </span>
      ) : (
        <span className="slug" style={{ color: "var(--text-3)" }}>
          선택된 파일 없음
        </span>
      )}
    </div>
  );
}
