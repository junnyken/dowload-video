import { useCallback, useEffect, useRef, useState } from 'react'
import { douyinChannelCookieApi } from '../../api/douyinChannelCookie'
import type { ChannelCookieStatus, ChannelCookieTest } from '../../api/douyinChannelCookie'
import { Card } from '../../shared/Card'
import { Button } from '../../shared/Button'
import { SectionHeader } from '../../shared/SectionHeader'
import { StatCard } from '../../shared/StatCard'

const INPUT =
  'h-9 w-full rounded-control border border-line bg-surface px-3 text-sm text-fg placeholder:text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'
const AREA =
  'min-h-[96px] w-full rounded-control border border-line bg-surface px-3 py-2 font-mono text-xs text-fg placeholder:text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'

const REASON_VI: Record<string, string> = {
  no_cookie: 'chưa có cookie',
  rate_cap: 'đã chạm trần lượt/giờ',
  bad_test_url: 'link kênh thử không hợp lệ',
  'empty 200': 'Douyin trả trang rỗng (cookie bị từ chối)',
}

const when = (t: number | null | undefined) => (t ? new Date(t * 1000).toLocaleString('vi-VN') : '—')

function testText(t: ChannelCookieTest) {
  if (t.ok) return `Dùng được: đọc ${t.pages} trang, ${t.items} video`
  return `Không dùng được: ${REASON_VI[t.reason] ?? t.reason ?? 'lỗi'}`
}

export function DouyinChannelCookiePanel() {
  const [st, setSt] = useState<ChannelCookieStatus | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [perHour, setPerHour] = useState('')
  const [testUrl, setTestUrl] = useState('')
  const [busy, setBusy] = useState<'save' | 'test' | 'settings' | 'delete' | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async (resetForm: boolean) => {
    try {
      const s = await douyinChannelCookieApi.status()
      setSt(s)
      if (resetForm) {
        setPerHour(String(s.per_hour))
        setTestUrl(s.test_url)
      }
      setLoadErr(null)
    } catch (e) {
      setLoadErr((e as Error).message)
    }
  }, [])

  useEffect(() => {
    load(true)
    const t = setInterval(() => load(false), 30_000)
    return () => clearInterval(t)
  }, [load])

  async function run(kind: NonNullable<typeof busy>, fn: () => Promise<string>) {
    setBusy(kind)
    setMsg(null)
    try {
      const ok = await fn()
      setMsg({ ok: true, text: ok })
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    if (f.size > 2_000_000) {
      setMsg({ ok: false, text: 'Tệp quá lớn (tối đa 2 MB).' })
      return
    }
    const r = new FileReader()
    r.onload = () => setText(String(r.result ?? ''))
    r.readAsText(f)
    e.target.value = ''
  }

  const save = () => run('save', async () => {
    if (!text.trim()) throw new Error('Hãy dán hoặc chọn tệp cookie.')
    const out = await douyinChannelCookieApi.save(text)
    setText('')
    setSt(out.status)
    if (!out.test.ok) throw new Error(`Đã lưu ${out.names.length} cookie nhưng thử ${testText(out.test).toLowerCase()}`)
    return `Đã lưu ${out.names.length} cookie douyin.com. ${testText(out.test)}`
  })

  const test = () => run('test', async () => {
    const t = await douyinChannelCookieApi.test()
    await load(false)
    if (!t.ok) throw new Error(testText(t))
    return testText(t)
  })

  const saveSettings = (enabled?: boolean) => run('settings', async () => {
    const n = Number(perHour)
    if (!Number.isInteger(n) || n < 1) throw new Error('Trần lượt/giờ phải là số nguyên dương.')
    await douyinChannelCookieApi.saveSettings({
      per_hour: n,
      test_url: testUrl.trim(),
      ...(enabled !== undefined ? { enabled } : {}),
    })
    await load(true)
    return 'Đã lưu'
  })

  const remove = () => run('delete', async () => {
    if (!window.confirm('Xoá cookie lấy cả kênh? Khách sẽ quay về cách cũ (trang đầu + tiện ích).')) return 'Đã huỷ'
    setSt(await douyinChannelCookieApi.remove())
    return 'Đã xoá cookie'
  })

  const live = st?.has_cookie && st.enabled && !st.dead
  const stateText = !st?.has_cookie ? 'Chưa có' : st.dead ? 'Đã chết' : !st.enabled ? 'Đang tắt' : 'Đang dùng'

  return (
    <Card as="section">
      <SectionHeader
        title="Cookie Douyin để lấy cả kênh"
        subtitle="Máy chủ dùng cookie này lật hết các trang của một kênh cho người dùng đã đăng nhập. Không dùng để tải video."
      />
      <div className="flex flex-col gap-4 p-4">
        {loadErr && <p role="alert" className="text-sm text-danger">{loadErr}</p>}

        {st && (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatCard label="Trạng thái" value={stateText}
                tone={live ? 'success' : st.has_cookie ? 'danger' : 'warning'} />
              <StatCard label="Lần thử gần nhất"
                tone={st.last_test ? (st.last_test.ok ? 'success' : 'danger') : 'default'}
                value={st.last_test ? (st.last_test.ok ? 'Dùng được' : 'Hỏng') : '—'}
                hint={st.last_test ? `${st.last_test.pages} trang · ${when(st.last_test.at)}` : undefined} />
              <StatCard label="Thành công / lỗi" value={`${st.ok} / ${st.fail}`}
                hint={st.fail_streak ? `${st.fail_streak} lỗi liên tiếp` : undefined} />
              <StatCard label="Lượt gọi giờ này" value={`${st.calls_this_hour}/${st.per_hour}`} />
            </div>
            <div className="space-y-1 text-xs text-fg-muted">
              {st.has_cookie && <p>Lưu lúc {when(st.saved_at)} · {st.names.length} cookie: {st.names.join(', ')}</p>}
              {st.last_fail && <p className="break-words">Lỗi gần nhất ({when(st.last_fail_at)}): {st.last_fail}</p>}
              {st.dead && <p className="text-danger">Douyin từ chối 3 lần liền. Hãy dán cookie mới.</p>}
            </div>
          </>
        )}

        <div className="flex flex-col gap-2">
          <label htmlFor="dcc-text" className="text-xs text-fg-2">
            Dán cookie (cookies.txt, JSON hoặc chuỗi "a=b; c=d"). Chỉ cookie của douyin.com được giữ, các trang khác bị bỏ.
          </label>
          <textarea id="dcc-text" className={AREA} value={text} spellCheck={false} autoComplete="off"
            placeholder=".douyin.com	TRUE	/	TRUE	1827130477	ttwid	…"
            onChange={(e) => setText(e.target.value)} />
          <div className="flex flex-wrap items-center gap-2">
            <input ref={fileRef} type="file" accept=".txt,.json,text/plain,application/json" className="hidden" onChange={onFile} />
            <Button onClick={() => fileRef.current?.click()} disabled={busy !== null}>Chọn tệp…</Button>
            <Button variant="primary" onClick={save} disabled={busy !== null || !text.trim()}>
              {busy === 'save' ? 'Đang lưu và thử...' : 'Lưu và thử'}
            </Button>
            <Button onClick={test} disabled={busy !== null || !st?.has_cookie}>
              {busy === 'test' ? 'Đang thử...' : 'Thử ngay'}
            </Button>
            {st?.has_cookie && (
              <Button variant="danger" onClick={remove} disabled={busy !== null}>Xoá cookie</Button>
            )}
          </div>
        </div>

        {st && (
          <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); saveSettings() }}>
            <label className="flex items-center gap-2 text-sm text-fg">
              <input type="checkbox" checked={st.enabled} disabled={busy !== null}
                onChange={(e) => saveSettings(e.target.checked)} />
              Bật lấy cả kênh bằng cookie này
            </label>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-[160px_1fr]">
              <div className="flex flex-col gap-1">
                <label htmlFor="dcc-ph" className="text-xs text-fg-2">Trần lượt gọi/giờ</label>
                <input id="dcc-ph" type="number" min={1} className={INPUT} value={perHour}
                  onChange={(e) => setPerHour(e.target.value)} />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="dcc-url" className="text-xs text-fg-2">Kênh dùng để thử</label>
                <input id="dcc-url" className={INPUT} value={testUrl} autoComplete="off"
                  placeholder="https://www.douyin.com/user/MS4w…" onChange={(e) => setTestUrl(e.target.value)} />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={busy !== null}>{busy === 'settings' ? 'Đang lưu...' : 'Lưu cài đặt'}</Button>
            </div>
          </form>
        )}

        {msg && (
          <p role="status" className={`text-sm ${msg.ok ? 'text-success' : 'text-danger'}`}>{msg.text}</p>
        )}

        <p className="text-[11px] text-fg-muted">
          Mỗi trang là một lượt gọi (18 video). Một kênh 200 video tốn khoảng 12 lượt. Cookie lưu mã hoá; trang này chỉ hiện tên cookie, không hiện giá trị. Khi cookie hỏng hoặc chạm trần, hệ thống quay về cách cũ: trang đầu và tiện ích.
        </p>
      </div>
    </Card>
  )
}
