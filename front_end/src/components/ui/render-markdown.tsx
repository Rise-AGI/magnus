// front_end/src/components/ui/render-markdown.tsx
"use client";

{/* 感谢 @wjsoj 卫同学！love you ❤ */}

import React from "react";
import Markdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import remarkParse from "remark-parse";
import rehypeStringify from "rehype-stringify";
// @ts-ignore
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
// @ts-ignore
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import { cn } from "@/lib/utils";
import { CopyableText } from "@/components/ui/copyable-text";


interface RenderMarkdownProps {
  content: string;
  className?: string;
}


const RenderMarkdown = React.memo(function RenderMarkdown({
  content,
  className,
}: RenderMarkdownProps) {

  const processed = content.replace(/^---\n([\s\S]*?)\n---/, (_m, yaml) => "```yaml\n" + yaml.trim() + "\n```\n");

  const markdownComponents = {
    h1: ({ className, ...props }: any) => (
      <h1
        className={cn(
          "text-2xl font-bold tracking-tight text-zinc-100 mb-4",
          className,
        )}
        {...props}
      />
    ),
    h2: ({ className, ...props }: any) => (
      <h2
        className={cn(
          "mt-6 border-b border-zinc-800 pb-2 text-xl font-semibold tracking-tight first:mt-0 text-zinc-100",
          className,
        )}
        {...props}
      />
    ),
    h3: ({ className, ...props }: any) => (
      <h3
        className={cn(
          "mt-4 text-lg font-semibold tracking-tight text-zinc-100",
          className,
        )}
        {...props}
      />
    ),
    h4: ({ className, ...props }: any) => (
      <h4
        className={cn(
          "mt-3 text-base font-semibold tracking-tight text-zinc-100",
          className,
        )}
        {...props}
      />
    ),
    p: ({ className, ...props }: any) => (
      <p
        className={cn(
          "leading-7 text-sm [&:not(:first-child)]:mt-3 text-zinc-300 break-words min-w-0",
          className,
        )}
        {...props}
      />
    ),
    ul: ({ className, ...props }: any) => (
      <ul
        className={cn("my-3 ml-6 list-disc [&>li]:mt-1 text-sm text-zinc-300", className)}
        {...props}
      />
    ),
    ol: ({ className, ...props }: any) => (
      <ol
        className={cn("my-3 ml-6 list-decimal [&>li]:mt-1 text-sm text-zinc-300", className)}
        {...props}
      />
    ),
    li: ({ className, ...props }: any) => (
      <li className={cn("text-sm text-zinc-300", className)} {...props} />
    ),
    blockquote: ({ className, ...props }: any) => (
      <blockquote
        className={cn("mt-4 border-l-2 border-zinc-700 pl-4 italic text-sm text-zinc-400", className)}
        {...props}
      />
    ),
    img: ({ className, ...props }: any) => (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        className={cn("rounded-md border border-zinc-800 bg-zinc-950 max-w-full h-auto", className)}
        {...props}
        alt=""
      />
    ),
    hr: ({ ...props }) => (
      <hr className="my-4 border-zinc-800" {...props} />
    ),
    table: ({ className, ...props }: any) => (
      <div className="my-4 w-full overflow-x-auto">
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
    pre: ({ children }: any) => (
      <div className="not-prose max-w-full overflow-x-auto">
        {children}
      </div>
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
      const codeString = String(children).replace(/\n$/, "");

      return match ? (
        <div className="relative group mt-4 rounded-lg overflow-hidden border border-zinc-800 max-w-full">
          <div className="flex items-center justify-between px-4 py-2 bg-zinc-900 border-b border-zinc-800">
            <span className="text-xs font-mono text-zinc-400">
              {match[1]}
            </span>
            <CopyableText
              text="Copy"
              copyValue={codeString}
              variant="id"
              className="text-zinc-500 hover:text-zinc-300"
            />
          </div>
          <div className="custom-scrollbar overflow-x-auto">
            <SyntaxHighlighter
              {...rest}
              PreTag="div"
              language={match[1]}
              style={vscDarkPlus}
              customStyle={{ margin: 0, borderRadius: 0, padding: '1rem', background: '#09090b' }}
            >
              {codeString}
            </SyntaxHighlighter>
          </div>
        </div>
      ) : (
        <code
          {...rest}
          className={cn(
            "bg-zinc-800/50 px-1.5 py-0.5 rounded text-zinc-200 font-mono text-xs border border-zinc-700/50 break-all",
            className,
          )}
        >
          {children}
        </code>
      );
    },
  };

  return (
    <div className={cn("markdown-body max-w-full overflow-hidden [&>*:first-child]:!mt-0", className)}>
      <Markdown
        remarkPlugins={[remarkMath, remarkGfm, remarkParse]}
        rehypePlugins={[rehypeKatex, rehypeRaw, rehypeStringify]}
        components={markdownComponents}
      >
        {processed}
      </Markdown>
    </div>
  );
}, (prevProps, nextProps) => {
  return prevProps.content === nextProps.content && prevProps.className === nextProps.className;
});

export default RenderMarkdown;
