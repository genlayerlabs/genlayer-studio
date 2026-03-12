import { fetchBackend } from '@/lib/fetchBackend';
import { DashboardContent, type Stats } from './DashboardContent';
import { Card, CardContent } from '@/components/ui/card';

export default async function DashboardPage() {
  try {
    const stats = await fetchBackend<Stats>('/stats', { revalidate: 0 });
    return <DashboardContent stats={stats} />;
  } catch (err) {
    return (
      <Card className="border-destructive">
        <CardContent className="p-6">
          <h2 className="font-bold mb-2 text-destructive">Error loading dashboard</h2>
          <p className="text-destructive/80">
            {err instanceof Error ? err.message : 'Unknown error'}
          </p>
        </CardContent>
      </Card>
    );
  }
}
