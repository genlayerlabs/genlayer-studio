import { redirect } from 'next/navigation';

export default async function ContractDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/address/${id}`);
}
