import { useCallback, useEffect, useRef, useState } from 'react'
import { douyinChannelCookieApi } from '../../api/douyinChannelCookie'
import type { ChannelCookie, ChannelCookieStatus, ChannelCookieTest } from '../../api/douyinChannelCookie'
import { Card } from '../../shared/Card'
import { Button } from '../../shared/Button'
import { SectionHeader } from '../../shared/SectionHeader'
import { StatCard } from '../../shared/StatCard'
import { TABLE, TABLE_SCROLL, TD, TD_NUM, TH, TH_NUM, TR } from '../../shared/tableStyles'

const INPUT =
  'h-9 w-full rounded-control border border-line bg-surface px-3 text-sm text-fg placeholder:text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'
const AREA =
  'min-h-[96px] w-full rounded-control border border-line bg-surface px-3 py-2 font-mono text-xs text-fg placeholder:text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'

const REASON_VI: Record<string, string> = {
  no_cookie: 'không còn cookie này',
  rate_cap: 'đã chạm trần lượt/giờ',
  bad_test_url: 'link kênh thử không hợp lệ',
  'empty 200': 'Douyin trả trang rỗng (cookie bị từ chối)',
}

const when = (t: number | null | undefined) => (t ? new Date(t * 1000).toLocaleString('vi-VN') : '—')

function testText(t: ChannelCookieTest) {
  if (t.ok) return `Dùng được: đọc ${t.pages} trang, ${t.items} video`
  return `Không dùng được: ${REASON_VI[t.reason] ?? t.reason ?? 'lỗi'}`
}

const nameOf = (c: ChannelCookie, i: number) => c.label || `Cookie ${i + 1}`

export function DouyinChannelCookiePanel() {
  const [st, setSt] = useState<ChannelCookieStatus | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [label, setLabel] = useState('')
  const [perHour, setPerHour] = useState('')
  const [testUrl, setTestUrl] = useState('')
  const [busy, setBusy] = useState<string | null>(null) // 'add' | 'settings' | 'test:<id>' | 'delete:<id>'
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

  async function run(kind: string, fn: () => Promise<string>) {
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

  const add = () => run('add', async () => {
    if (!text.trim()) throw new Error('Hãy dán hoặc chọn tệp cookie.')
    const out = await douyinChannelCookieApi.add(text, label.trim())
    setText('')
    setLabel('')
    setSt(out.status)
    if (!out.test.ok) throw new Error(`Đã lưu ${out.names.length} cookie nhưng thử ${testText(out.test).toLowerCase()}`)
    return `Đã lưu ${out.names.length} cookie douyin.com. ${testText(out.test)}`
  })

  const test = (c: ChannelCookie, i: number) => run(`test:${c.id}`, async () => {
    const t = await douyinChannelCookieApi.test(c.id)
    await load(false)
    if (!t.ok) throw new Error(`${nameOf(c, i)}: ${testText(t)}`)
    return `${nameOf(c, i)}: ${testText(t)}`
  })

  const remove = (c: ChannelCookie, i: number) => run(`delete:${c.id}`, async () => {
    if (!window.confirm(`Xoá ${nameOf(c, i)}?`)) return 'Đã huỷ'
    setSt(await douyinChannelCookieApi.remove(c.id))
    return `Đã xoá ${nameOf(c, i)}`
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

  const total = st?.cookies.length ?? 0
  const live = st?.live ?? 0
  const auto = st?.auto

  return (
    <Card as="section">
      <SectionHeader
        title="Cookie Douyin để lấy cả kênh"
        subtitle="Máy chủ dùng lần lượt các cookie này để lật hết các trang của một kênh cho người dùng đã đăng nhập. Không dùng để tải video."
      />
      <div className="flex flex-col gap-4 p-4">
        {loadErr && <p role="alert" className="text-sm text-danger">{loadErr}</p>}

        {st && (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Cookie còn sống" value={`${live}/${total}`}
              tone={!st.enabled ? 'warning' : live ? 'success' : 'danger'}
              hint={!st.enabled ? 'Đang tắt' : !total ? 'Chưa có cookie' : live ? 'Đang dùng' : 'Tất cả đã chết'} />
            <StatCard label="Tự thử gần nhất"
              tone={!auto ? 'default' : auto.ip_blocked ? 'warning' : auto.dead ? 'danger' : 'success'}
              value={!auto ? '—' : auto.ip_blocked ? 'IP bị chặn' : `${auto.ok}/${auto.tested} sống`}
              hint={auto ? `${when(auto.at)} · 6 giờ/lần` : '6 giờ/lần'} />
            <StatCard label="Lượt gọi giờ này" value={`${st.calls_this_hour}/${st.per_hour}`} />
            <StatCard label="Bật lấy cả kênh" value={st.enabled ? 'Bật' : 'Tắt'} tone={st.enabled ? 'success' : 'warning'} />
          </div>
        )}
        {auto?.ip_blocked && (
          <p className="text-xs text-warning">
            Lần tự thử gần nhất mọi cookie đều bị từ chối: thường là Douyin đang chặn IP máy chủ, nên không cookie nào bị đánh dấu chết.
          </p>
        )}

        {st && total > 0 && (
          <div className={TABLE_SCROLL}>
            <table className={TABLE}>
              <thead>
                <tr>
                  <th className={TH}>Cookie</th>
                  <th className={TH}>Trạng thái</th>
                  <th className={TH}>Lần thử gần nhất</th>
                  <th className={TH_NUM}>Thành công / lỗi</th>
                  <th className={TH}>Lưu lúc</th>
                  <th className={TH}></th>
                </tr>
              </thead>
              <tbody>
                {st.cookies.map((c, i) => (
                  <tr key={c.id} className={TR}>
                    <td className={TD}>
                      <p className="font-medium">{nameOf(c, i)}</p>
                      <p className="text-[11px] text-fg-muted" title={c.names.join(', ')}>{c.names.length} cookie</p>
                    </td>
                    <td className={TD}>
                      <span className={c.dead ? 'text-danger' : 'text-success'}>{c.dead ? 'Đã chết' : 'Đang dùng'}</span>
                      {c.last_fail && (
                        <p className="max-w-[260px] truncate text-[11px] text-fg-muted" title={c.last_fail}>
                          Lỗi {when(c.last_fail_at)}: {c.last_fail}
                        </p>
                      )}
                    </td>
                    <td className={TD}>
                      {c.last_test ? (
                        <>
                          <span className={c.last_test.ok ? 'text-success' : 'text-danger'}>
                            {c.last_test.ok ? `Dùng được · ${c.last_test.pages} trang` : 'Hỏng'}
                          </span>
                          <p className="text-[11px] text-fg-muted">{when(c.last_test.at)}{c.last_test.auto ? ' · tự thử' : ''}</p>
                        </>
                      ) : '—'}
                    </td>
                    <td className={TD_NUM}>{c.ok} / {c.fail}</td>
                    <td className={TD}><span className="text-xs">{when(c.saved_at)}</span></td>
                    <td className={TD}>
                      <div className="flex justify-end gap-2">
                        <Button onClick={() => test(c, i)} disabled={busy !== null}>
                          {busy === `test:${c.id}` ? 'Đang thử...' : 'Thử'}
                        </Button>
                        <Button variant="danger" onClick={() => remove(c, i)} disabled={busy !== null}>Xoá</Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <label htmlFor="dcc-text" className="text-xs text-fg-2">
            Thêm cookie (cookies.txt, JSON hoặc chuỗi "a=b; c=d"). Chỉ cookie của douyin.com được giữ, các trang khác bị bỏ. Cookie trùng ttwid với một cookie đã có sẽ thay cookie đó.
          </label>
          <input className={INPUT} value={label} maxLength={60} autoComplete="off" aria-label="Tên gợi nhớ"
            placeholder="Tên gợi nhớ (tuỳ chọn), vd: Chrome máy văn phòng" onChange={(e) => setLabel(e.target.value)} />
          <textarea id="dcc-text" className={AREA} value={text} spellCheck={false} autoComplete="off"
            placeholder=".douyin.com	TRUE	/	TRUE	1827130477	ttwid	…"
            onChange={(e) => setText(e.target.value)} />
          <div className="flex flex-wrap items-center gap-2">
            <input ref={fileRef} type="file" accept=".txt,.json,text/plain,application/json" className="hidden" onChange={onFile} />
            <Button onClick={() => fileRef.current?.click()} disabled={busy !== null}>Chọn tệp…</Button>
            <Button variant="primary" onClick={add} disabled={busy !== null || !text.trim()}>
              {busy === 'add' ? 'Đang lưu và thử...' : 'Thêm và thử'}
            </Button>
          </div>
        </div>

        {st && (
          <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); saveSettings() }}>
            <label className="flex items-center gap-2 text-sm text-fg">
              <input type="checkbox" checked={st.enabled} disabled={busy !== null}
                onChange={(e) => saveSettings(e.target.checked)} />
              Bật lấy cả kênh bằng các cookie này
            </label>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-[160px_1fr]">
              <div className="flex flex-col gap-1">
                <label htmlFor="dcc-ph" className="text-xs text-fg-2">Trần lượt gọi/giờ (chung)</label>
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
          Mỗi trang là một lượt gọi (18 video); một kênh 200 video tốn khoảng 12 lượt. Các cookie được dùng lần lượt; cookie nào bị từ chối thì chuyển ngay sang cookie kế tiếp, bị từ chối 3 lần liền thì coi là chết. Mỗi 6 giờ máy chủ tự thử từng cookie (2 trang); hỏng 2 lần liền trong một lượt tự thử là đánh dấu chết, thử lại được thì sống lại. Douyin chặn theo IP máy chủ, nên thêm cookie giúp không gián đoạn chứ không tăng trần lượt. Cookie lưu mã hoá; trang này chỉ hiện tên cookie, không hiện giá trị.
        </p>
      </div>
    </Card>
  )
}
