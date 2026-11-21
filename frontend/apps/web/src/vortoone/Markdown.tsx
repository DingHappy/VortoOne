import { Fragment, type ReactNode } from "react";
import { COMPUTER_DOWNLOAD, INLINE, parseMarkdown, SAFE_URL } from "@vortoone/ui/markdown";
import { CodeBlock } from "../components/CodeBlock";

// Reuse the upstream Markdown parser and code viewer, without its organization-specific link resolvers.
function inline(text: string): ReactNode {
  const parts: ReactNode[] = [];
  let previous = 0;
  const pattern = new RegExp(INLINE.source, `${INLINE.flags}g`);
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    parts.push(text.slice(previous, index));
    const value = match[0];
    if (value.startsWith("`"))
      parts.push(<code key={index}>{value.slice(1, -1)}</code>);
    else if (value.startsWith("**") || value.startsWith("__"))
      parts.push(<strong key={index}>{value.slice(2, -2)}</strong>);
    else if (value.startsWith("~~"))
      parts.push(<s key={index}>{value.slice(2, -2)}</s>);
    else if (value.startsWith("[")) {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(value);
      const href = link?.[2] ?? "";
      parts.push(
        link && COMPUTER_DOWNLOAD.test(href) ? (
          <a key={index} href={href} download>{link[1]}</a>
        ) : link && SAFE_URL.test(href) ? (
          <a key={index} href={href} target="_blank" rel="noreferrer">
            {link[1]}
          </a>
        ) : (
          value
        ),
      );
    } else if (/^https?:\/\//.test(value))
      parts.push(
        <a key={index} href={value} target="_blank" rel="noreferrer">
          {value}
        </a>,
      );
    else if (value.startsWith("*") || value.startsWith("_"))
      parts.push(<em key={index}>{value.slice(1, -1)}</em>);
    else parts.push(value);
    previous = index + value.length;
  }
  parts.push(text.slice(previous));
  return parts;
}
export function Markdown({ text }: { text: string }) {
  return (
    <div className="md md--message">
      {parseMarkdown(text, { breaks: true, details: true }).map(
        (block, index) => {
          switch (block.type) {
            case "paragraph":
              return <p key={index}>{inline(block.text)}</p>;
            case "heading":
              return (
                <p className="md__h" key={index}>
                  <strong>{inline(block.text)}</strong>
                </p>
              );
            case "code":
              return (
                <CodeBlock key={index} lang={block.lang} text={block.text} />
              );
            case "quote":
              return <blockquote key={index}>{inline(block.text)}</blockquote>;
            case "list": {
              const Tag = block.ordered ? "ol" : "ul";
              return (
                <Tag key={index}>
                  {block.items.map((item, i) => (
                    <li key={i}>{inline(item)}</li>
                  ))}
                </Tag>
              );
            }
            case "table":
              return (
                <div className="vo-table" key={index}>
                  <table>
                    <thead>
                      <tr>
                        {block.header.map((cell, i) => (
                          <th key={i}>{inline(cell)}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {block.rows.map((row, i) => (
                        <tr key={i}>
                          {row.map((cell, j) => (
                            <td key={j}>{inline(cell)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            case "rule":
              return <hr key={index} />;
            case "details":
              return (
                <details key={index}>
                  <summary>{block.summary}</summary>
                  <Markdown text={block.body} />
                </details>
              );
            case "embed":
              return <Fragment key={index}>{block.target}</Fragment>;
          }
        },
      )}
    </div>
  );
}
