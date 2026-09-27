import Link from "next/link";
import { Icon } from "@/components/Icon";
import { TOOLS } from "@/lib/tools";

export default function Home() {
  return (
    <main className="home">
      <h1 className="hero">Settlement tools for merchants</h1>
      <p className="hero-sub">
        Free, and nothing you upload is kept.
      </p>

      <div className="grid">
        {TOOLS.map((tool) => (
          <Link key={tool.slug} href={`/${tool.slug}`} className="card">
            <span className="card-icon">
              <Icon name={tool.icon} size={22} />
            </span>
            <span className="card-name">{tool.name}</span>
            <span className="card-does">{tool.does}</span>
            <span className="card-needs">{tool.needs}</span>
          </Link>
        ))}
      </div>
    </main>
  );
}
