'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { apiLogin } from '@/lib/api';
import { saveToken, isAuthenticated } from '@/lib/auth';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const data = await apiLogin(username, password);
      saveToken(data.access_token, data.username);
      router.replace('/');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error de autenticación');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-md">
        {/* Logo / título */}
        <div className="text-center mb-8">
          <h1 className="text-5xl font-bold brand-gradient">📺 TELERÍN</h1>
          <p className="mt-2 text-gray-500">
            Sistema Multi-Agente sobre <span className="font-semibold">TeleRadio</span>
          </p>
          <p className="text-sm text-gray-400">Base de datos histórica (1958-1965)</p>
        </div>

        {/* Card de login */}
        <div className="bg-white rounded-2xl shadow-lg p-8">
          <h2 className="text-xl font-semibold mb-6 text-gray-700">Iniciar sesión</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">
                Usuario
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
                autoComplete="username"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">
                Contraseña
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
                autoComplete="current-password"
                required
              />
            </div>

            {error && (
              <div className="bg-red-50 border-l-4 border-red-400 text-red-700 p-3 text-sm rounded">
                ❌ {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-gradient-to-r from-brand-orange to-brand-amber text-white py-2.5 rounded-lg font-medium hover:opacity-90 disabled:opacity-50 transition"
            >
              {loading ? 'Accediendo...' : 'Entrar'}
            </button>
          </form>
        </div>


      </div>
    </div>
  );
}
