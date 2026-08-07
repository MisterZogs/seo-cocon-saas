"use client";

import { Fragment, type ReactNode } from "react";

/**
 * Rendu markdown minimal, sans dépendance et sans `dangerouslySetInnerHTML`.
 *
 * Les articles générés utilisent titres, listes, tableaux, citations, gras, et
 * les marqueurs `[[INTERNAL_LINK:slug|ancre]]` du maillage. Les afficher en
 * monospace brut rendait le livrable illisible — en particulier les tableaux,
 * très présents dans les articles SEO.
 *
 * Volontairement partiel : on couvre ce que le pipeline produit réellement,
 * pas la spec CommonMark. Tout ce qui n'est pas reconnu retombe en paragraphe.
 */

const INLINE = new RegExp(
  [
    String.raw`\[\[INTERNAL_LINK:([^|\]]+)\|([^\]]+)\]\]`, // 1: slug, 2: ancre
    String.raw`\*\*([^*]+)\*\*`, //                            3: gras
    String.raw`\[([^\]]+)\]\(([^)]+)\)`, //                    4: texte, 5: url
    String.raw`\*([^*]+)\*`, //                                6: italique
    String.raw`\x60([^\x60]+)\x60`, //                         7: code
  ].join("|"),
  "g",
);

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;

  INLINE.lastIndex = 0;
  while ((match = INLINE.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const key = `${keyPrefix}-${i++}`;
    const [, slug, anchor, bold, linkText, url, italic, code] = match;

    if (slug !== undefined) {
      // Lien de maillage — on montre l'ancre, la cible reste consultable au survol
      out.push(
        <span
          key={key}
          title={`Lien interne → /${slug}`}
          className="font-medium text-indigo-700 underline decoration-indigo-400 decoration-dotted underline-offset-2 dark:text-indigo-300 dark:decoration-indigo-600"
        >
          {anchor}
        </span>,
      );
    } else if (bold !== undefined) {
      out.push(
        <strong key={key} className="font-semibold text-foreground">
          {bold}
        </strong>,
      );
    } else if (linkText !== undefined) {
      out.push(
        <a
          key={key}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sky-700 underline underline-offset-2 hover:text-sky-900 dark:text-sky-300"
        >
          {linkText}
        </a>,
      );
    } else if (italic !== undefined) {
      out.push(
        <em key={key} className="italic">
          {italic}
        </em>,
      );
    } else if (code !== undefined) {
      out.push(
        <code
          key={key}
          className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]"
        >
          {code}
        </code>,
      );
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function splitRow(line: string): string[] {
  return line
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((c) => c.trim());
}

/**
 * Reconnaît un séparateur de tableau (`|---|---|`) et renvoie ce qui le suit
 * sur la même ligne — `""` quand il est propre, `null` quand la ligne n'est pas
 * un séparateur.
 *
 * Le modèle colle parfois la première ligne de données au séparateur en
 * émettant un `n` littéral au lieu d'un retour à la ligne (constaté en prod sur
 * `|---|---|---|n| Taille | ... |`). On récupère la ligne au lieu de perdre le
 * tableau entier.
 */
function separatorRest(line: string): string | null {
  const m = /^\s*\|?(?:\s*:?-+:?\s*\|)+/.exec(line);
  if (!m) return null;
  const rest = line.slice(m[0].length);
  const pipe = rest.indexOf("|");
  return pipe === -1 ? "" : rest.slice(pipe);
}

export function Markdown({ content }: { content: string }) {
  const lines = content.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  // Un `|` en début de ligne ne suffit pas : sans séparateur en dessous ce
  // n'est pas un tableau, et la ligne doit rester du texte ordinaire.
  const startsTable = (idx: number) =>
    (lines[idx]?.trim().startsWith("|") ?? false) &&
    separatorRest(lines[idx + 1] ?? "") !== null;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i++;
      continue;
    }

    // Titres
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const text = renderInline(heading[2], `h${key}`);
      const styles: Record<number, string> = {
        1: "text-2xl font-bold tracking-tight mt-2 mb-4 text-foreground",
        2: "text-xl font-bold tracking-tight mt-8 mb-3 pb-2 border-b text-foreground",
        3: "text-base font-semibold mt-6 mb-2 text-foreground",
      };
      const cls = styles[level] ?? "text-sm font-semibold mt-4 mb-2 text-foreground";
      const Tag = (`h${Math.min(level + 1, 6)}`) as "h2";
      blocks.push(
        <Tag key={key++} className={cls}>
          {text}
        </Tag>,
      );
      i++;
      continue;
    }

    // Tableaux — très présents dans les articles SEO
    if (line.trim().startsWith("|") && isTableSeparator(lines[i + 1] ?? "")) {
      const header = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push(
        <div key={key++} className="my-4 overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/60">
              <tr>
                {header.map((h, hi) => (
                  <th
                    key={hi}
                    className="px-3 py-2 text-left font-semibold text-foreground"
                  >
                    {renderInline(h, `th${key}-${hi}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-t">
                  {r.map((c, ci) => (
                    <td key={ci} className="px-3 py-2 align-top">
                      {renderInline(c, `td${key}-${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // Citations
    if (line.trim().startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      blocks.push(
        <blockquote
          key={key++}
          className="my-4 border-l-4 border-teal-400 bg-teal-50/60 py-2 pl-4 pr-3 text-sm italic dark:bg-teal-950/30"
        >
          {renderInline(quote.join(" "), `q${key}`)}
        </blockquote>,
      );
      continue;
    }

    // Listes à puces
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      blocks.push(
        <ul key={key++} className="my-3 space-y-1.5 pl-1">
          {items.map((it, ii) => (
            <li key={ii} className="flex gap-2.5 text-sm leading-relaxed">
              <span className="mt-2 size-1.5 shrink-0 rounded-full bg-sky-400" />
              <span>{renderInline(it, `li${key}-${ii}`)}</span>
            </li>
          ))}
        </ul>,
      );
      continue;
    }

    // Listes numérotées
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      blocks.push(
        <ol key={key++} className="my-3 space-y-1.5">
          {items.map((it, ii) => (
            <li key={ii} className="flex gap-2.5 text-sm leading-relaxed">
              <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-sky-100 text-xs font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-300">
                {ii + 1}
              </span>
              <span>{renderInline(it, `oli${key}-${ii}`)}</span>
            </li>
          ))}
        </ol>,
      );
      continue;
    }

    // Séparateur horizontal
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push(<hr key={key++} className="my-6 border-border" />);
      i++;
      continue;
    }

    // Paragraphe : on accumule jusqu'à la ligne vide ou le prochain bloc
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,6})\s/.test(lines[i]) &&
      !lines[i].trim().startsWith("|") &&
      !lines[i].trim().startsWith(">") &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    if (para.length) {
      blocks.push(
        <p key={key++} className="my-3 text-sm leading-relaxed text-foreground/90">
          {renderInline(para.join(" "), `p${key}`)}
        </p>,
      );
    }
  }

  return <Fragment>{blocks}</Fragment>;
}
