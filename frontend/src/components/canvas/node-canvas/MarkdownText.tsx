import { Fragment, type ReactNode } from "react";

/** 行内：`**粗体**`、`*斜体*`、`` `代码` ``。只产出 React 元素，不走 innerHTML。 */
function inline(text: string, keyPrefix: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)/g;
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > last) parts.push(text.slice(last, start));
    const token = match[0];
    const key = `${keyPrefix}-${index++}`;
    if (token.startsWith("**")) parts.push(<strong key={key} className="font-semibold text-text">{token.slice(2, -2)}</strong>);
    else if (token.startsWith("`")) parts.push(<code key={key} className="rounded bg-white/10 px-1 py-0.5 text-[0.92em] text-text">{token.slice(1, -1)}</code>);
    else parts.push(<em key={key}>{token.slice(1, -1)}</em>);
    last = start + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "rule" }
  | { kind: "list"; items: string[] }
  | { kind: "paragraph"; lines: string[] };

function parse(source: string): Block[] {
  const blocks: Block[] = [];
  let list: string[] | null = null;
  let paragraph: string[] = [];
  const flushParagraph = () => {
    if (paragraph.length > 0) blocks.push({ kind: "paragraph", lines: paragraph });
    paragraph = [];
  };
  const flushList = () => {
    if (list) blocks.push({ kind: "list", items: list });
    list = null;
  };
  for (const raw of source.split("\n")) {
    const line = raw.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
    } else if (/^\s*([-*_])\1{2,}\s*$/.test(line) || /^\s*\.{4,}\s*$/.test(line)) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "rule" });
    } else if (bullet) {
      flushParagraph();
      (list ??= []).push(bullet[1]);
    } else if (line.trim() === "") {
      flushParagraph();
      flushList();
    } else {
      flushList();
      paragraph.push(line.trim());
    }
  }
  flushParagraph();
  flushList();
  return blocks;
}

const HEADING_CLS = ["", "text-[1.25em] font-semibold text-text", "text-[1.12em] font-semibold text-text", "font-semibold text-text", "font-medium text-text-2"];

/** 文档节点与全文弹窗共用的 markdown 渲染：标题、粗体、列表、分隔线与段落，足够显示来源、剧本与单元拆解。 */
export function MarkdownText({ source, className = "" }: { source: string; className?: string }) {
  const blocks = parse(source);
  return (
    <div className={`space-y-[0.6em] leading-relaxed text-text-3 ${className}`}>
      {blocks.map((block, i) => {
        const key = `b${i}`;
        if (block.kind === "heading") {
          return <p key={key} className={`pt-[0.3em] ${HEADING_CLS[block.level]}`}>{inline(block.text, key)}</p>;
        }
        if (block.kind === "rule") return <hr key={key} className="border-hairline" />;
        if (block.kind === "list") {
          return (
            <ul key={key} className="list-disc space-y-[0.25em] pl-[1.2em]">
              {block.items.map((item, j) => <li key={`${key}-${j}`}>{inline(item, `${key}-${j}`)}</li>)}
            </ul>
          );
        }
        return (
          <p key={key}>
            {block.lines.map((line, j) => (
              <Fragment key={`${key}-${j}`}>
                {j > 0 ? <br /> : null}
                {inline(line, `${key}-${j}`)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
