import Link from "next/link";
import { Icon } from "@/components/Icon";
import { Info } from "@/components/Info";
import { bySlug } from "@/lib/tools";

export function ToolShell({ slug, children }: { slug: string; children: React.ReactNode }) {
  const tool = bySlug(slug);
  if (!tool) return null;
  return (
    <main className="tool rise">
      <Link href="/" className="back">← All tools</Link>
      <div className="tool-head">
        <span className="tool-icon"><Icon name={tool.icon} size={24} /></span>
        <div>
          <h1 className="tool-name">
            {tool.name}
            <Info label={`How ${tool.name} works`}>{tool.about}</Info>
          </h1>
          <p className="tool-does">{tool.does}</p>
        </div>
      </div>
      {children}
    </main>
  );
}
