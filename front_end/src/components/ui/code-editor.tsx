// front_end/src/components/ui/code-editor.tsx
"use client";

import Editor from "react-simple-code-editor";
import { highlight, languages } from "prismjs";
import "prismjs/components/prism-clike";
import "prismjs/components/prism-python";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-yaml";
import "prismjs/components/prism-json";
import "prismjs/components/prism-markdown";
import "prismjs/themes/prism-okaidia.css";

const EXT_TO_LANG: Record<string, string> = {
  ".py": "python",
  ".sh": "bash",
  ".bash": "bash",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".json": "json",
  ".md": "markdown",
};

function detectLanguage(filename?: string): string {
  if (!filename) return "python";
  const dot = filename.lastIndexOf(".");
  if (dot === -1) return "python";
  return EXT_TO_LANG[filename.substring(dot)] || "python";
}

function getCommentPrefix(lang: string): string {
  if (lang === "json") return "// ";
  if (lang === "markdown") return "";
  return "# ";
}

interface CodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  filename?: string;
  language?: string;
  readOnly?: boolean;
  minHeight?: string;
  className?: string;
}

export function CodeEditor({
  value,
  onChange,
  filename,
  language,
  readOnly = false,
  minHeight = "100%",
  className = "",
}: CodeEditorProps) {
  const lang = language || detectLanguage(filename);
  const grammar = languages[lang] || languages.python;
  const commentPrefix = getCommentPrefix(lang);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (readOnly) return;

    // react-simple-code-editor fires onKeyDown on the wrapper <div>,
    // but we need the underlying <textarea> for selection manipulation.
    const target = (e.target as HTMLElement).tagName === "TEXTAREA"
      ? (e.target as HTMLTextAreaElement)
      : (e.currentTarget.querySelector("textarea") as HTMLTextAreaElement | null);
    if (!target) return;
    const { value: val, selectionStart, selectionEnd } = target;

    const firstLineStart = val.lastIndexOf("\n", selectionStart - 1) + 1;
    const lastLineEnd = val.indexOf("\n", selectionEnd);
    const blockEnd = lastLineEnd === -1 ? val.length : lastLineEnd;
    const selectedBlock = val.substring(firstLineStart, blockEnd);
    const lines = selectedBlock.split("\n");
    const hasMultipleLines = lines.length > 1 || selectionStart !== selectionEnd;

    if (e.key === "Tab") {
      e.preventDefault();

      if (e.shiftKey) {
        let totalRemoved = 0;
        let firstLineRemoved = 0;
        const newLines = lines.map((line, i) => {
          const match = line.match(/^( {1,4})/);
          if (match) {
            const removed = match[1].length;
            totalRemoved += removed;
            if (i === 0) firstLineRemoved = removed;
            return line.substring(removed);
          }
          return line;
        });

        const newBlock = newLines.join("\n");
        target.setSelectionRange(firstLineStart, blockEnd);
        document.execCommand("insertText", false, newBlock);

        setTimeout(() => {
          const newStart = Math.max(firstLineStart, selectionStart - firstLineRemoved);
          const newEnd = Math.max(newStart, selectionEnd - totalRemoved);
          target.selectionStart = newStart;
          target.selectionEnd = hasMultipleLines ? newEnd : newStart;
        }, 0);
      } else {
        if (hasMultipleLines) {
          const newLines = lines.map(line => "    " + line);
          const newBlock = newLines.join("\n");
          target.setSelectionRange(firstLineStart, blockEnd);
          document.execCommand("insertText", false, newBlock);

          setTimeout(() => {
            target.selectionStart = selectionStart + 4;
            target.selectionEnd = selectionEnd + lines.length * 4;
          }, 0);
        } else {
          document.execCommand("insertText", false, "    ");
        }
      }
    }

    if ((e.metaKey || e.ctrlKey) && e.key === "/" && commentPrefix) {
      e.preventDefault();

      const allCommented = lines.every(line => line.trim() === "" || line.trimStart().startsWith(commentPrefix.trimEnd()));

      let totalDelta = 0;
      let firstLineDelta = 0;
      const newLines = lines.map((line, i) => {
        if (allCommented) {
          const pattern = new RegExp(`^(\\s*)${commentPrefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}?`);
          const match = line.match(pattern);
          if (match) {
            const newLine = match[1] + line.substring(match[0].length);
            const delta = line.length - newLine.length;
            totalDelta -= delta;
            if (i === 0) firstLineDelta = -delta;
            return newLine;
          }
          return line;
        } else {
          const match = line.match(/^(\s*)(.*)$/);
          const indent = match ? match[1] : "";
          const content = match ? match[2] : line;
          if (content === "") return line;
          const newLine = indent + commentPrefix + content;
          totalDelta += commentPrefix.length;
          if (i === 0) firstLineDelta = commentPrefix.length;
          return newLine;
        }
      });

      const newBlock = newLines.join("\n");
      target.setSelectionRange(firstLineStart, blockEnd);
      document.execCommand("insertText", false, newBlock);

      setTimeout(() => {
        const newStart = Math.max(firstLineStart, selectionStart + firstLineDelta);
        const newEnd = hasMultipleLines ? selectionEnd + totalDelta : newStart;
        target.selectionStart = newStart;
        target.selectionEnd = newEnd;
      }, 0);
    }
  };

  return (
    <Editor
      value={value}
      onValueChange={readOnly ? () => {} : (v) => onChange?.(v)}
      highlight={code => highlight(code, grammar, lang)}
      padding={24}
      onKeyDown={handleKeyDown}
      className={`prism-editor font-mono text-sm leading-relaxed ${className}`}
      style={{
        fontFamily: '"Fira Code", "Fira Mono", monospace',
        fontSize: 14,
        backgroundColor: "transparent",
        minHeight,
        ...(readOnly ? { pointerEvents: "none" as const } : {}),
      }}
      textareaClassName="focus:outline-none"
      disabled={readOnly}
    />
  );
}
