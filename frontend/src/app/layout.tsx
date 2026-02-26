import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'TeleRadio - Multi-Agent Search',
  description: 'Sistema multi-agente de búsqueda en el archivo histórico de TeleRadio (1958-1965)',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body className="min-h-screen bg-gray-50">{children}</body>
    </html>
  );
}
