import { Link } from "react-router-dom";
import { api, STATE_TONE, STATE_WORD, agentName, when } from "../lib/api";
import { useLive } from "../lib/desk";
import { Badge, Card, Empty, Mono, Skeleton } from "../components/ui";

export default function Items() {
  const { data } = useLive(() => api.workItems(), [], 3000);
  return (
    <Card title="Every work item" pad={false} aside={data && <Badge tone="neutral">{data.count}</Badge>}>
      {!data && <div className="space-y-2 p-5"><Skeleton /><Skeleton /><Skeleton /></div>}
      {data && data.count === 0 && <div className="p-5"><Empty title="No work yet" hint="Trigger an event on the home page." /></div>}
      {data && data.count > 0 && (
        <table className="w-full text-[14px]">
          <thead className="text-left text-[11.5px] uppercase tracking-[0.06em] text-ink-3">
            <tr className="border-b border-line">
              <th className="px-5 py-2 font-medium">Item</th><th className="py-2 font-medium">What</th><th className="py-2 font-medium">State</th>
              <th className="py-2 font-medium">Seat</th><th className="py-2 font-medium">Parent</th><th className="py-2 pr-5 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {data.work_items.map((w: any) => (
              <tr key={w.id} className="border-b border-line last:border-0 hover:bg-surface-2">
                <td className="px-5 py-2.5"><Link to={`/items/${w.id}`} className="num font-medium underline-offset-4 hover:underline">{w.id}</Link></td>
                <td className="py-2.5">{w.title}</td>
                <td className="py-2.5"><Badge tone={STATE_TONE[w.state] as any}>{STATE_WORD[w.state] || w.state}</Badge></td>
                <td className="py-2.5 text-ink-2">{w.seat ? agentName(w.seat) : "–"}</td>
                <td className="py-2.5"><Mono>{w.parent_id || "–"}</Mono></td>
                <td className="py-2.5 pr-5"><Mono>{when(w.updated_at)}</Mono></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
