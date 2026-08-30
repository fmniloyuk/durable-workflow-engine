import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Durable Workflow Engine',
  description: 'Monitoring console for durable-workflow-engine',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
