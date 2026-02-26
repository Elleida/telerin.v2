'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { isAuthenticated, getUserRole } from '@/lib/auth';
import {
  apiGetUsers, apiCreateUser, apiUpdateUser, apiDeleteUser, UserPublic,
  apiGetStats, StatsData, DayStat,
} from '@/lib/api';

const ROLES = ['user', 'admin'];

function formatDate(s?: string) {
  if (!s) return '—';
  try { return new Date(s).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' }); }
  catch { return s; }
}

function fmt(n: number, dec = 1) { return n.toFixed(dec); }

// ── Mini bar chart (CSS only) ───────────────────────────────────────────────
function BarChart({ data }: { data: DayStat[] }) {
  if (!data.length) return <p className="text-gray-500 text-sm">Sin datos</p>;
  const max = Math.max(...data.map(d => d.count), 1);
  return (
    <div className="flex items-end gap-1 h-28 overflow-x-auto pb-5">
      {data.map(d => (
        <div key={d.date} className="flex flex-col items-center gap-0.5 min-w-[28px]"
          title={`${d.date} — Total: ${d.count} | 👍 ${d.up}  👎 ${d.down}`}>
          <span className="text-[9px] text-gray-500">{d.count}</span>
          <div className="w-full flex flex-col-reverse rounded-sm overflow-hidden"
            style={{ height: `${Math.max(4, Math.round((d.count / max) * 72))}px` }}>
            <div className="w-full bg-green-600" style={{ height: `${Math.round((d.up / Math.max(d.count,1)) * 100)}%` }} />
            <div className="w-full bg-red-700"   style={{ height: `${Math.round((d.down / Math.max(d.count,1)) * 100)}%` }} />
          </div>
          <span className="text-[8px] text-gray-600 whitespace-nowrap" style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)', height: '3rem', lineHeight: 1 }}>
            {d.date.slice(5)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Stats Tab ───────────────────────────────────────────────────────────────
function StatsTab() {
  const [stats, setStats] = useState<StatsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiGetStats()
      .then(setStats)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-center text-gray-400 py-16">Cargando estadísticas…</div>;
  if (error)   return <div className="p-4 bg-red-900/40 border border-red-700 rounded-lg text-red-300">{error}</div>;
  if (!stats)  return null;

  const pct = (n: number) => stats.total ? Math.round((n / stats.total) * 100) : 0;

  return (
    <div className="space-y-8">

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: 'Consultas totales',  value: String(stats.total),                          color: 'text-white' },
          { label: '👍 Positivas',       value: `${stats.up} (${pct(stats.up)}%)`,            color: 'text-green-400' },
          { label: '👎 Negativas',       value: `${stats.down} (${pct(stats.down)}%)`,        color: 'text-red-400' },
          { label: 'Fuentes promedio',   value: fmt(stats.avg_num_sources) + ' / consulta',   color: 'text-blue-400' },
        ].map(c => (
          <div key={c.label} className="bg-gray-800 border border-gray-700 rounded-xl p-4">
            <p className="text-xs text-gray-400 mb-1">{c.label}</p>
            <p className={`text-2xl font-bold ${c.color}`}>{c.value}</p>
          </div>
        ))}
      </div>

      {/* Timings */}
      <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
        <h3 className="text-sm font-semibold text-gray-300 mb-4">⏱ Tiempos promedio (segundos)</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: 'BD / búsqueda', val: stats.avg_db_search_s },
            { label: 'Reranking',     val: stats.avg_reranking_s },
            { label: 'Respuesta LLM', val: stats.avg_response_s },
            { label: 'Total',         val: stats.avg_total_s },
          ].map(t => (
            <div key={t.label}>
              <p className="text-xs text-gray-500 mb-1">{t.label}</p>
              <p className="text-2xl font-semibold text-yellow-400">{fmt(t.val, 2)}s</p>
            </div>
          ))}
        </div>
      </div>

      {/* Activity chart */}
      <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
        <h3 className="text-sm font-semibold text-gray-300 mb-4">📅 Actividad diaria (últimos 30 días)</h3>
        <BarChart data={stats.by_day} />
        <p className="text-xs text-gray-600 mt-1">🟢 Positivas &nbsp; 🔴 Negativas</p>
      </div>

      {/* By user */}
      <div className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-gray-700">
          <h3 className="text-sm font-semibold text-gray-300">👤 Actividad por usuario</h3>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-gray-900 text-gray-400 text-xs uppercase tracking-wider">
            <tr>
              <th className="px-4 py-2 text-left">Usuario</th>
              <th className="px-4 py-2 text-right">Consultas</th>
              <th className="px-4 py-2 text-right">👍</th>
              <th className="px-4 py-2 text-right">👎</th>
              <th className="px-4 py-2 text-right">% positivo</th>
              <th className="px-4 py-2 text-right">T. resp. medio</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700">
            {stats.by_user.map(u => (
              <tr key={u.username} className="hover:bg-gray-700/40">
                <td className="px-4 py-2 font-medium text-white">{u.username}</td>
                <td className="px-4 py-2 text-right text-gray-300">{u.count}</td>
                <td className="px-4 py-2 text-right text-green-400">{u.up}</td>
                <td className="px-4 py-2 text-right text-red-400">{u.down}</td>
                <td className="px-4 py-2 text-right">
                  <span className={u.count ? (u.up / u.count > 0.7 ? 'text-green-400' : 'text-yellow-400') : 'text-gray-500'}>
                    {u.count ? Math.round((u.up / u.count) * 100) : 0}%
                  </span>
                </td>
                <td className="px-4 py-2 text-right text-yellow-400">{fmt(u.avg_response_s, 1)}s</td>
              </tr>
            ))}
            {stats.by_user.length === 0 && (
              <tr><td colSpan={6} className="text-center text-gray-500 py-6">Sin datos</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Recent queries */}
      <div className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-gray-700">
          <h3 className="text-sm font-semibold text-gray-300">🕐 Consultas recientes</h3>
        </div>
        <div className="divide-y divide-gray-700">
          {stats.recent.map((e, i) => (
            <div key={i} className="px-5 py-3 hover:bg-gray-700/30">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-200 truncate">{e.query}</p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {formatDate(e.ts)} · <span className="text-gray-400">{e.user}</span>
                    {' · '}{e.num_sources} fuentes · {fmt(e.total_s, 1)}s
                    {e.llm_model && <> · <span className="text-purple-400">{e.llm_model}</span></>}
                  </p>
                </div>
                <span className="text-lg shrink-0">{e.rating === 'up' ? '👍' : '👎'}</span>
              </div>
            </div>
          ))}
          {stats.recent.length === 0 && (
            <p className="text-center text-gray-500 py-6 text-sm">Sin datos</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Users Tab ───────────────────────────────────────────────────────────────
function UsersTab() {
  const [users, setUsers] = useState<UserPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editId, setEditId] = useState<string | null>(null);
  const [editData, setEditData] = useState({ username: '', email: '', role: 'user', password: '', first_name: '', last_name: '' });
  const [showNew, setShowNew] = useState(false);
  const [newData, setNewData] = useState({ username: '', email: '', role: 'user', password: '', first_name: '', last_name: '' });
  const [newError, setNewError] = useState('');
  const [confirmDel, setConfirmDel] = useState<string | null>(null);

  const loadUsers = useCallback(async () => {
    setLoading(true); setError('');
    try { setUsers(await apiGetUsers()); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error cargando usuarios'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadUsers(); }, [loadUsers]);

  function startEdit(u: UserPublic) {
    setEditId(u.id);
    setEditData({ username: u.username, email: u.email ?? '', role: u.role, password: '',
                  first_name: u.first_name ?? '', last_name: u.last_name ?? '' });
  }

  async function saveEdit() {
    if (!editId) return;
    const payload: Record<string, string> = {
      first_name: editData.first_name,
      last_name:  editData.last_name,
    };
    if (editData.username) payload.username = editData.username;
    if (editData.email)    payload.email    = editData.email;
    if (editData.role)     payload.role     = editData.role;
    if (editData.password) payload.password = editData.password;
    try {
      const updated = await apiUpdateUser(editId, payload);
      setUsers(us => us.map(u => u.id === editId ? updated : u));
      setEditId(null);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error actualizando usuario'); }
  }

  async function doDelete(id: string) {
    setConfirmDel(null);
    try {
      const res = await apiDeleteUser(id);
      if (!res.ok && res.status !== 204) throw new Error(`Error ${res.status}`);
      setUsers(us => us.filter(u => u.id !== id));
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error eliminando usuario'); }
  }

  async function createUser() {
    setNewError('');
    if (!newData.username || !newData.password) { setNewError('Usuario y contraseña son obligatorios'); return; }
    try {
      const created = await apiCreateUser(newData);
      setUsers(us => [...us, created]);
      setShowNew(false);
      setNewData({ username: '', email: '', role: 'user', password: '', first_name: '', last_name: '' });
    } catch (e: unknown) { setNewError(e instanceof Error ? e.message : 'Error creando usuario'); }
  }

  const inp   = "w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500";
  const inpSm = "w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-sm focus:outline-none focus:border-blue-500";

  return (
    <div className="space-y-6">
      {error && (
        <div className="p-3 bg-red-900/50 border border-red-700 rounded-lg text-red-300 text-sm flex justify-between">
          <span>{error}</span>
          <button onClick={() => setError('')} className="text-red-400 hover:text-red-200 ml-4">✕</button>
        </div>
      )}

      <div className="flex justify-end">
        <button onClick={() => { setShowNew(v => !v); setNewError(''); }}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors">
          {showNew ? 'Cancelar' : '+ Nuevo usuario'}
        </button>
      </div>

      {showNew && (
        <div className="p-5 bg-gray-800 border border-gray-700 rounded-xl">
          <h2 className="text-lg font-semibold mb-4 text-gray-200">Nuevo usuario</h2>
          {newError && <p className="text-red-400 text-sm mb-3">{newError}</p>}
          <div className="grid grid-cols-2 gap-4 mb-4">
            {([
              { label: 'Usuario *',    key: 'username',   type: 'text',     ph: 'nombre_usuario' },
              { label: 'Email',        key: 'email',      type: 'email',    ph: 'usuario@ejemplo.com' },
              { label: 'Nombre',       key: 'first_name', type: 'text',     ph: 'Nombre' },
              { label: 'Apellidos',    key: 'last_name',  type: 'text',     ph: 'Apellidos' },
              { label: 'Contraseña *', key: 'password',   type: 'password', ph: '••••••••' },
            ] as const).map(f => (
              <div key={f.key}>
                <label className="block text-xs text-gray-400 mb-1">{f.label}</label>
                <input className={inp} type={f.type} placeholder={f.ph}
                  value={newData[f.key]}
                  onChange={e => setNewData(d => ({ ...d, [f.key]: e.target.value }))} />
              </div>
            ))}
            <div>
              <label className="block text-xs text-gray-400 mb-1">Rol</label>
              <select className={inp} value={newData.role} onChange={e => setNewData(d => ({ ...d, role: e.target.value }))}>
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          </div>
          <button onClick={createUser} className="px-6 py-2 bg-green-700 hover:bg-green-600 rounded-lg text-sm font-medium transition-colors">
            Crear usuario
          </button>
        </div>
      )}

      {loading ? (
        <div className="text-center text-gray-400 py-16">Cargando…</div>
      ) : (
        <div className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-gray-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="px-4 py-3 text-left">ID</th>
                <th className="px-4 py-3 text-left">Usuario</th>
                <th className="px-4 py-3 text-left">Nombre</th>
                <th className="px-4 py-3 text-left">Apellidos</th>
                <th className="px-4 py-3 text-left">Email</th>
                <th className="px-4 py-3 text-left">Rol</th>
                <th className="px-4 py-3 text-left">Creado</th>
                <th className="px-4 py-3 text-right">Acciones</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {users.length === 0 && (
                <tr><td colSpan={8} className="text-center text-gray-500 py-8">Sin usuarios</td></tr>
              )}
              {users.map(u => editId === u.id ? (
                <tr key={u.id} className="bg-gray-700/50">
                  <td className="px-4 py-3 text-gray-500 font-mono text-xs">{u.id.slice(0, 8)}…</td>
                  <td className="px-4 py-2"><input className={inpSm} value={editData.username} onChange={e => setEditData(d => ({ ...d, username: e.target.value }))} /></td>
                  <td className="px-4 py-2"><input className={inpSm} placeholder="Nombre" value={editData.first_name} onChange={e => setEditData(d => ({ ...d, first_name: e.target.value }))} /></td>
                  <td className="px-4 py-2"><input className={inpSm} placeholder="Apellidos" value={editData.last_name} onChange={e => setEditData(d => ({ ...d, last_name: e.target.value }))} /></td>
                  <td className="px-4 py-2"><input className={inpSm} type="email" value={editData.email} onChange={e => setEditData(d => ({ ...d, email: e.target.value }))} /></td>
                  <td className="px-4 py-2">
                    <select className="bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-sm focus:outline-none" value={editData.role} onChange={e => setEditData(d => ({ ...d, role: e.target.value }))}>
                      {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </td>
                  <td className="px-4 py-2"><input className={inpSm} type="password" placeholder="Nueva contraseña" value={editData.password} onChange={e => setEditData(d => ({ ...d, password: e.target.value }))} /></td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex justify-end gap-2">
                      <button onClick={saveEdit} className="px-3 py-1 bg-green-700 hover:bg-green-600 rounded text-xs font-medium">Guardar</button>
                      <button onClick={() => setEditId(null)} className="px-3 py-1 bg-gray-600 hover:bg-gray-500 rounded text-xs font-medium">Cancelar</button>
                    </div>
                  </td>
                </tr>
              ) : confirmDel === u.id ? (
                <tr key={u.id} className="bg-red-900/20">
                  <td colSpan={7} className="px-4 py-3 text-red-300 text-sm">
                    ¿Eliminar usuario <strong>{u.username}</strong>? Esta acción no se puede deshacer.
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      <button onClick={() => doDelete(u.id)} className="px-3 py-1 bg-red-700 hover:bg-red-600 rounded text-xs font-medium">Eliminar</button>
                      <button onClick={() => setConfirmDel(null)} className="px-3 py-1 bg-gray-600 hover:bg-gray-500 rounded text-xs font-medium">Cancelar</button>
                    </div>
                  </td>
                </tr>
              ) : (
                <tr key={u.id} className="hover:bg-gray-700/40 transition-colors">
                  <td className="px-4 py-3 text-gray-500 font-mono text-xs">{u.id.slice(0, 8)}…</td>
                  <td className="px-4 py-3 font-medium text-white">{u.username}</td>
                  <td className="px-4 py-3 text-gray-300">{u.first_name ?? '—'}</td>
                  <td className="px-4 py-3 text-gray-300">{u.last_name ?? '—'}</td>
                  <td className="px-4 py-3 text-gray-400">{u.email ?? '—'}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${u.role === 'admin' ? 'bg-purple-900/60 text-purple-300' : 'bg-gray-700 text-gray-300'}`}>
                      {u.role}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-400">{formatDate(u.created_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      <button onClick={() => startEdit(u)} className="px-3 py-1 bg-blue-700 hover:bg-blue-600 rounded text-xs font-medium">Editar</button>
                      <button onClick={() => setConfirmDel(u.id)} className="px-3 py-1 bg-red-800 hover:bg-red-700 rounded text-xs font-medium">Eliminar</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-4 py-2 text-xs text-gray-600 text-right border-t border-gray-700">
            {users.length} usuario{users.length !== 1 ? 's' : ''}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────────────────────
export default function AdminPage() {
  const router = useRouter();
  const [tab, setTab] = useState<'users' | 'stats'>('users');

  useEffect(() => {
    if (!isAuthenticated()) { router.replace('/login'); return; }
    if (getUserRole() !== 'admin') { router.replace('/'); return; }
  }, []);

  const tabs = [
    { id: 'users'  as const, label: '👥 Usuarios' },
    { id: 'stats'  as const, label: '📊 Estadísticas de uso' },
  ];

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Panel de Administración</h1>
            <p className="text-gray-400 mt-0.5 text-sm">Sistema multi-agente TeleRín</p>
          </div>
          <button onClick={() => router.push('/')}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition-colors">
            ← Volver al chat
          </button>
        </div>

        <div className="flex gap-1 mb-6 bg-gray-800 p-1 rounded-xl w-fit">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`px-5 py-2 rounded-lg text-sm font-medium transition-colors ${tab === t.id ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-white'}`}>
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'users' ? <UsersTab /> : <StatsTab />}
      </div>
    </div>
  );
}
