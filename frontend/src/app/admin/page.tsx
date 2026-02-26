'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { isAuthenticated, getUserRole } from '@/lib/auth';
import {
  apiGetUsers,
  apiCreateUser,
  apiUpdateUser,
  apiDeleteUser,
  UserPublic,
} from '@/lib/api';

const ROLES = ['user', 'admin'];

function formatDate(s?: string) {
  if (!s) return '—';
  try { return new Date(s).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' }); }
  catch { return s; }
}

export default function AdminPage() {
  const router = useRouter();
  const [users, setUsers] = useState<UserPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Edit state
  const [editId, setEditId] = useState<string | null>(null);
  const [editData, setEditData] = useState({ username: '', email: '', role: 'user', password: '', first_name: '', last_name: '' });

  // New user form
  const [showNew, setShowNew] = useState(false);
  const [newData, setNewData] = useState({ username: '', email: '', role: 'user', password: '', first_name: '', last_name: '' });
  const [newError, setNewError] = useState('');

  // Delete confirmation
  const [confirmDel, setConfirmDel] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated()) { router.replace('/login'); return; }
    if (getUserRole() !== 'admin') { router.replace('/'); return; }
    loadUsers();
  }, []);

  async function loadUsers() {
    setLoading(true);
    setError('');
    try {
      const data = await apiGetUsers();
      setUsers(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Error cargando usuarios');
    } finally {
      setLoading(false);
    }
  }

  function startEdit(u: UserPublic) {
    setEditId(u.id);
    setEditData({ username: u.username, email: u.email ?? '', role: u.role, password: '',
                  first_name: u.first_name ?? '', last_name: u.last_name ?? '' });
  }

  async function saveEdit() {
    if (!editId) return;
    const payload: Record<string, string> = {};
    if (editData.username) payload.username = editData.username;
    if (editData.email) payload.email = editData.email;
    if (editData.role) payload.role = editData.role;
    if (editData.password) payload.password = editData.password;
    if (editData.first_name !== undefined) payload.first_name = editData.first_name;
    if (editData.last_name !== undefined) payload.last_name = editData.last_name;
    try {
      const updated = await apiUpdateUser(editId, payload);
      setUsers(us => us.map(u => u.id === editId ? updated : u));
      setEditId(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Error actualizando usuario');
    }
  }

  async function doDelete(id: string) {
    setConfirmDel(null);
    try {
      const res = await apiDeleteUser(id);
      if (!res.ok && res.status !== 204) throw new Error(`Error ${res.status}`);
      setUsers(us => us.filter(u => u.id !== id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Error eliminando usuario');
    }
  }

  async function createUser() {
    setNewError('');
    if (!newData.username || !newData.password) {
      setNewError('Usuario y contraseña son obligatorios'); return;
    }
    try {
      const created = await apiCreateUser(newData);
      setUsers(us => [...us, created]);
      setShowNew(false);
      setNewData({ username: '', email: '', role: 'user', password: '', first_name: '', last_name: '' });
    } catch (e: unknown) {
      setNewError(e instanceof Error ? e.message : 'Error creando usuario');
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-white">Panel de Administración</h1>
            <p className="text-gray-400 mt-1">Gestión de usuarios del sistema</p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => { setShowNew(v => !v); setNewError(''); }}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
            >
              {showNew ? 'Cancelar' : '+ Nuevo usuario'}
            </button>
            <button
              onClick={() => router.push('/')}
              className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition-colors"
            >
              ← Volver al chat
            </button>
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="mb-4 p-3 bg-red-900/50 border border-red-700 rounded-lg text-red-300 text-sm flex justify-between">
            <span>{error}</span>
            <button onClick={() => setError('')} className="text-red-400 hover:text-red-200 ml-4">✕</button>
          </div>
        )}

        {/* New user form */}
        {showNew && (
          <div className="mb-6 p-5 bg-gray-800 border border-gray-700 rounded-xl">
            <h2 className="text-lg font-semibold mb-4 text-gray-200">Nuevo usuario</h2>
            {newError && <p className="text-red-400 text-sm mb-3">{newError}</p>}
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-xs text-gray-400 mb-1">Usuario *</label>
                <input
                  className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={newData.username}
                  onChange={e => setNewData(d => ({ ...d, username: e.target.value }))}
                  placeholder="nombre_usuario"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Email</label>
                <input
                  className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={newData.email}
                  onChange={e => setNewData(d => ({ ...d, email: e.target.value }))}
                  placeholder="usuario@ejemplo.com"
                  type="email"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Nombre</label>
                <input
                  className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={newData.first_name}
                  onChange={e => setNewData(d => ({ ...d, first_name: e.target.value }))}
                  placeholder="Nombre"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Apellidos</label>
                <input
                  className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={newData.last_name}
                  onChange={e => setNewData(d => ({ ...d, last_name: e.target.value }))}
                  placeholder="Apellidos"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Contraseña *</label>
                <input
                  className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={newData.password}
                  onChange={e => setNewData(d => ({ ...d, password: e.target.value }))}
                  type="password"
                  placeholder="••••••••"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Rol</label>
                <select
                  className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={newData.role}
                  onChange={e => setNewData(d => ({ ...d, role: e.target.value }))}
                >
                  {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
            </div>
            <button
              onClick={createUser}
              className="px-6 py-2 bg-green-700 hover:bg-green-600 rounded-lg text-sm font-medium transition-colors"
            >
              Crear usuario
            </button>
          </div>
        )}

        {/* Users table */}
        {loading ? (
          <div className="text-center text-gray-400 py-16">Cargando...</div>
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
                  <tr>
                    <td colSpan={8} className="text-center text-gray-500 py-8">Sin usuarios</td>
                  </tr>
                )}
                {users.map(u => (
                  editId === u.id ? (
                    /* ── Edit row ── */
                    <tr key={u.id} className="bg-gray-750 bg-gray-700/50">
                      <td className="px-4 py-3 text-gray-500 font-mono text-xs">{u.id.slice(0, 8)}…</td>
                      <td className="px-4 py-2">
                        <input
                          className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-sm focus:outline-none focus:border-blue-500"
                          value={editData.username}
                          onChange={e => setEditData(d => ({ ...d, username: e.target.value }))}
                        />
                      </td>
                      <td className="px-4 py-2">
                        <input
                          className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-sm focus:outline-none focus:border-blue-500"
                          value={editData.first_name}
                          onChange={e => setEditData(d => ({ ...d, first_name: e.target.value }))}
                          placeholder="Nombre"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <input
                          className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-sm focus:outline-none focus:border-blue-500"
                          value={editData.last_name}
                          onChange={e => setEditData(d => ({ ...d, last_name: e.target.value }))}
                          placeholder="Apellidos"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <input
                          className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-sm focus:outline-none focus:border-blue-500"
                          value={editData.email}
                          onChange={e => setEditData(d => ({ ...d, email: e.target.value }))}
                          type="email"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <select
                          className="bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-sm focus:outline-none focus:border-blue-500"
                          value={editData.role}
                          onChange={e => setEditData(d => ({ ...d, role: e.target.value }))}
                        >
                          {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                        </select>
                      </td>
                      <td className="px-4 py-2">
                        <input
                          className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-sm focus:outline-none focus:border-blue-500"
                          value={editData.password}
                          onChange={e => setEditData(d => ({ ...d, password: e.target.value }))}
                          type="password"
                          placeholder="Nueva contraseña (opcional)"
                        />
                      </td>
                      <td className="px-4 py-2 text-right">
                        <div className="flex justify-end gap-2">
                          <button onClick={saveEdit} className="px-3 py-1 bg-green-700 hover:bg-green-600 rounded text-xs font-medium transition-colors">Guardar</button>
                          <button onClick={() => setEditId(null)} className="px-3 py-1 bg-gray-600 hover:bg-gray-500 rounded text-xs font-medium transition-colors">Cancelar</button>
                        </div>
                      </td>
                    </tr>
                  ) : confirmDel === u.id ? (
                    /* ── Delete confirmation row ── */
                    <tr key={u.id} className="bg-red-900/20">
                      <td colSpan={7} className="px-4 py-3 text-red-300 text-sm">
                        ¿Eliminar usuario <strong>{u.username}</strong>? Esta acción no se puede deshacer.
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex justify-end gap-2">
                          <button onClick={() => doDelete(u.id)} className="px-3 py-1 bg-red-700 hover:bg-red-600 rounded text-xs font-medium transition-colors">Eliminar</button>
                          <button onClick={() => setConfirmDel(null)} className="px-3 py-1 bg-gray-600 hover:bg-gray-500 rounded text-xs font-medium transition-colors">Cancelar</button>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    /* ── Normal row ── */
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
                          <button onClick={() => startEdit(u)} className="px-3 py-1 bg-blue-700 hover:bg-blue-600 rounded text-xs font-medium transition-colors">Editar</button>
                          <button onClick={() => setConfirmDel(u.id)} className="px-3 py-1 bg-red-800 hover:bg-red-700 rounded text-xs font-medium transition-colors">Eliminar</button>
                        </div>
                      </td>
                    </tr>
                  )
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="mt-4 text-xs text-gray-600 text-right">{users.length} usuario{users.length !== 1 ? 's' : ''}</p>
      </div>
    </div>
  );
}
