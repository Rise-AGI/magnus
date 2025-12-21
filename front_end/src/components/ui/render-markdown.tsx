// front_end/src/components/ui/render-markdown.tsx
"use client";

{/* 感谢 @wjsoj 卫同学！love you ❤ */}

import Markdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import remarkParse from "remark-parse";
import rehypeStringify from "rehype-stringify";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import {
  vscDarkPlus,
} from "react-syntax-highlighter/dist/esm/styles/prism";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { Copy, ChevronDown, ChevronRight } from "lucide-react";

export default function RenderMarkdown({ content }: { content: string }) {

  const markdownComponents = {
    h1: ({ className, ...props }: any) => (
      <h1
        className={cn(
          "scroll-m-20 text-3xl font-extrabold tracking-tight lg:text-5xl text-zinc-100 mb-6",
          className,
        )}
        {...props}
      />
    ),
    h2: ({ className, ...props }: any) => (
      <h2
        className={cn(
          "mt-10 scroll-m-20 border-b border-zinc-800 pb-2 text-2xl font-semibold tracking-tight transition-colors first:mt-0 text-zinc-100",
          className,
        )}
        {...props}
      />
    ),
    h3: ({ className, ...props }: any) => (
      <h3
        className={cn(
          "mt-8 scroll-m-20 text-xl font-semibold tracking-tight text-zinc-100",
          className,
        )}
        {...props}
      />
    ),
    h4: ({ className, ...props }: any) => (
      <h4
        className={cn(
          "mt-4 scroll-m-20 text-lg font-semibold tracking-tight text-zinc-100",
          className,
        )}
        {...props}
      />
    ),
    p: ({ className, ...props }: any) => (
      <p
        className={cn(
          "leading-7 text-sm [&:not(:first-child)]:mt-4 text-zinc-300",
          className,
        )}
        {...props}
      />
    ),
    ul: ({ className, ...props }: any) => (
      <ul
        className={cn("my-6 ml-6 list-disc [&>li]:mt-2 text-sm text-zinc-300", className)}
        {...props}
      />
    ),
    ol: ({ className, ...props }: any) => (
      <ol
        className={cn("my-6 ml-6 list-decimal [&>li]:mt-2 text-sm text-zinc-300", className)}
        {...props}
      />
    ),
    li: ({ className, ...props }: any) => (
      <li className={cn("mt-2 text-sm text-zinc-300", className)} {...props} />
    ),
    blockquote: ({ className, ...props }: any) => (
      <blockquote
        className={cn("mt-6 border-l-2 border-zinc-700 pl-6 italic text-sm text-zinc-400", className)}
        {...props}
      />
    ),
    img: ({ className, ...props }: any) => (
      <img className={cn("rounded-md border border-zinc-800 bg-zinc-950", className)} {...props} alt="" />
    ),
    hr: ({ ...props }) => (
      <hr className="my-4 border-zinc-800" {...props} />
    ),
    table: ({ className, ...props }: any) => (
      <div className="my-6 w-full overflow-y-auto">
        <table className={cn("w-full border-collapse border border-zinc-800 text-sm", className)} {...props} />
      </div>
    ),
    tr: ({ className, ...props }: any) => (
      <tr
        className={cn("m-0 border-t border-zinc-800 p-0 even:bg-zinc-900/50", className)}
        {...props}
      />
    ),
    th: ({ className, ...props }: any) => (
      <th
        className={cn(
          "border border-zinc-800 px-4 py-2 text-left font-bold text-zinc-200 [&[align=center]]:text-center [&[align=right]]:text-right bg-zinc-900",
          className,
        )}
        {...props}
      />
    ),
    td: ({ className, ...props }: any) => (
      <td
        className={cn(
          "border border-zinc-800 px-4 py-2 text-left text-zinc-300 [&[align=center]]:text-center [&[align=right]]:text-right",
          className,
        )}
        {...props}
      />
    ),
    pre: ({ className, ...props }: any) => (
      <pre
        className={cn("mb-4 mt-4 overflow-x-auto rounded-lg py-2 bg-zinc-950 border border-zinc-800", className)}
        {...props}
      />
    ),
    a: ({ className, ...props }: any) => (
      <a
        className={cn(
          "font-medium underline underline-offset-4 text-blue-400 hover:text-blue-300 transition-all",
          className,
        )}
        {...props}
      />
    ),
    code: (props: any) => {
      const { children, className, node, ...rest } = props;
      const match = /language-(\w+)/.exec(className || "");
      const [copied, setCopied] = useState(false);

      const handleCopy = async () => {
        if (typeof children === "string") {
          await navigator.clipboard.writeText(children);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }
      };

      return match ? (
        <div className="relative group mt-6 rounded-lg overflow-hidden border border-zinc-800">
          <div className="flex items-center justify-between px-4 py-2 bg-zinc-900 border-b border-zinc-800">
            <span className="text-xs font-mono text-zinc-400">
              {match[1]}
            </span>
            <button
              onClick={handleCopy}
              className="relative flex items-center gap-1 text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              <Copy className="h-3.5 w-3.5" />
              {copied && (
                <span className="absolute right-0 -top-8 px-2 py-1 text-xs text-zinc-900 font-semibold bg-green-400 rounded shadow animate-in fade-in zoom-in duration-200">
                  Copied!
                </span>
              )}
            </button>
          </div>
          <SyntaxHighlighter
            {...rest}
            PreTag="div"
            children={String(children).replace(/\n$/, "")}
            language={match[1]}
            style={vscDarkPlus}
            customStyle={{ margin: 0, borderRadius: 0, padding: '1rem', background: '#09090b' }} // zinc-950
          />
        </div>
      ) : (
        <code {...rest} className={cn("bg-zinc-800/50 px-1.5 py-0.5 rounded text-zinc-200 font-mono text-xs border border-zinc-700/50", className)}>
          {children}
        </code>
      );
    },
    think: ({ ...props }: any) => {
      const [isOpen, setIsOpen] = useState(true);

      return (
        <div className="mb-4 rounded-lg border border-purple-500/20 bg-purple-500/5 overflow-hidden">
          <button
            onClick={() => setIsOpen(!isOpen)}
            className="flex items-center w-full px-4 py-2 text-left bg-purple-500/10 hover:bg-purple-500/20 transition-colors text-purple-200"
          >
            {isOpen ? (
              <ChevronDown className="h-4 w-4 mr-2 opacity-50" />
            ) : (
              <ChevronRight className="h-4 w-4 mr-2 opacity-50" />
            )}
            <span className="font-medium text-xs uppercase tracking-wider opacity-80">Reasoning Process</span>
          </button>
          {isOpen && (
            <div className="p-4 text-xs text-zinc-300 leading-relaxed border-t border-purple-500/10">
              {props.children}
            </div>
          )}
        </div>
      );
    },
  };

  return (
    <div className="markdown-body">
      <Markdown
        remarkPlugins={[remarkMath, remarkGfm, remarkParse]}
        rehypePlugins={[rehypeKatex, rehypeRaw, rehypeStringify]}
        components={markdownComponents}
      >
        {content}
      </Markdown>
    </div>
  );
}