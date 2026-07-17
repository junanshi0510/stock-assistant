import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  History,
  Save,
  ShieldCheck,
} from 'lucide-react'
import { fetchAgentRunFeedback, recordAgentRunFeedback } from '../api/agent'

const VERDICTS = [
  ['helpful', '有帮助'],
  ['partly_helpful', '部分有帮助'],
  ['not_helpful', '没有帮助'],
  ['data_issue', '数据有问题'],
  ['not_suitable', '不适合我'],
]

const DECISIONS = [
  ['undecided', '尚未决定'],
  ['observe', '继续观察'],
  ['hold', '维持持仓'],
  ['add', '计划增加'],
  ['reduce', '计划降低'],
  ['exit', '计划退出'],
  ['no_action', '不采取动作'],
]

const REASONS = [
  ['evidence_clear', '证据清楚'],
  ['fits_portfolio', '符合组合约束'],
  ['risk_too_high', '风险过高'],
  ['data_stale', '数据不够新'],
  ['missing_data', '关键数据缺失'],
  ['conclusion_unclear', '结论不清'],
  ['conflicts_with_plan', '与计划冲突'],
  ['already_acted', '已经执行'],
  ['other', '其他'],
]

const VERDICT_LABELS = Object.fromEntries(VERDICTS)
const DECISION_LABELS = Object.fromEntries(DECISIONS)
const REASON_LABELS = Object.fromEntries(REASONS)

function emptyForm() {
  return {
    feedback_verdict: '',
    user_decision: 'undecided',
    reason_codes: [],
    note: '',
    planned_review_at: '',
  }
}

function formFromEvent(event) {
  if (!event) return emptyForm()
  return {
    feedback_verdict: event.feedback_verdict || '',
    user_decision: event.user_decision || 'undecided',
    reason_codes: [...(event.reason_codes || [])],
    note: event.note || '',
    planned_review_at: event.planned_review_at || '',
  }
}

function comparableForm(form) {
  return JSON.stringify({
    feedback_verdict: form.feedback_verdict,
    user_decision: form.user_decision,
    reason_codes: [...form.reason_codes].sort(),
    note: form.note.trim(),
    planned_review_at: form.planned_review_at || '',
  })
}

function formatTime(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

function shortHash(value) {
  return value ? `${value.slice(0, 8)}...${value.slice(-6)}` : '-'
}

function FeedbackEvent({ event, latest = false }) {
  return (
    <div className="agent-journal-event">
      <div className="agent-journal-event-head">
        <div>
          <b>第 {event.sequence_no} 版</b>
          {latest && <span>当前</span>}
        </div>
        <time>{formatTime(event.created_at)}</time>
      </div>
      <div className="agent-journal-event-summary">
        <strong>{VERDICT_LABELS[event.feedback_verdict] || event.feedback_verdict}</strong>
        <span>{DECISION_LABELS[event.user_decision] || event.user_decision}</span>
        {event.planned_review_at && <span>复盘 {event.planned_review_at}</span>}
      </div>
      {event.reason_codes?.length > 0 && (
        <div className="agent-journal-event-reasons">
          {event.reason_codes.map((reason) => (
            <span key={reason}>{REASON_LABELS[reason] || reason}</span>
          ))}
        </div>
      )}
      {event.note && <p>{event.note}</p>}
      <small>结果绑定 {shortHash(event.run_result_sha256)} · 事件 {shortHash(event.event_hash)}</small>
    </div>
  )
}

export default function AgentDecisionJournal({ run }) {
  const [journal, setJournal] = useState(null)
  const [form, setForm] = useState(emptyForm)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setNotice('')
    fetchAgentRunFeedback(run.id)
      .then((data) => {
        if (!active) return
        setJournal(data)
        setForm(formFromEvent(data.latest))
      })
      .catch((requestError) => {
        if (active) setError(requestError.message || '决策日志加载失败')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [run.id])

  const dirty = useMemo(() => {
    if (!form.feedback_verdict) return false
    return comparableForm(form) !== comparableForm(formFromEvent(journal?.latest))
  }, [form, journal?.latest])

  const validationError = useMemo(() => {
    if (!form.feedback_verdict) return '请选择研判评价'
    if (form.feedback_verdict !== 'helpful' && form.reason_codes.length === 0) {
      return '请选择至少一个原因'
    }
    if (form.reason_codes.includes('other') && !form.note.trim()) return '请填写其他原因'
    return ''
  }, [form])

  function toggleReason(reason) {
    setForm((current) => ({
      ...current,
      reason_codes: current.reason_codes.includes(reason)
        ? current.reason_codes.filter((item) => item !== reason)
        : [...current.reason_codes, reason],
    }))
  }

  async function saveFeedback() {
    if (validationError || !dirty || saving) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const data = await recordAgentRunFeedback(run.id, {
        ...form,
        note: form.note.trim(),
        planned_review_at: form.planned_review_at || null,
        expected_previous_hash: journal?.latest?.event_hash || null,
      })
      setJournal(data)
      setForm(formFromEvent(data.latest))
      setNotice(data.created ? `已保存第 ${data.latest.sequence_no} 版决策` : '当前决策已保存')
    } catch (requestError) {
      setError(requestError.message || '决策保存失败')
      if (requestError.status === 409) {
        try {
          const latest = await fetchAgentRunFeedback(run.id)
          setJournal(latest)
          setForm(formFromEvent(latest.latest))
        } catch {
          // Keep the original conflict visible; a later page refresh can retry the read.
        }
      }
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <section className="agent-decision-journal agent-journal-loading" aria-busy="true">
        <History size={18} aria-hidden="true" />正在读取决策日志...
      </section>
    )
  }

  const verified = Boolean(journal?.verification?.verified)
  const canRecord = Boolean(journal?.can_record)

  return (
    <section className="agent-decision-journal">
      <div className="agent-journal-head">
        <div>
          <span className="eyebrow">Decision Journal</span>
          <h3>我的决策与复盘</h3>
        </div>
        <span className={verified ? 'verified' : 'invalid'}>
          {verified ? <ShieldCheck size={15} aria-hidden="true" /> : <AlertTriangle size={15} aria-hidden="true" />}
          {verified ? `日志链已校验 · ${journal.count || 0} 版` : '日志链校验失败'}
        </span>
      </div>

      {!journal?.eligibility?.eligible && (
        <div className="agent-journal-blocked">
          <AlertTriangle size={16} aria-hidden="true" />
          <span>{journal?.eligibility?.reason || '当前 Run 不能记录决策'}</span>
        </div>
      )}

      {canRecord && (
        <div className="agent-journal-form">
          <fieldset>
            <legend>这次研判是否有帮助</legend>
            <div className="agent-journal-segments" role="radiogroup" aria-label="研判评价">
              {VERDICTS.map(([value, label]) => (
                <button
                  type="button"
                  key={value}
                  role="radio"
                  aria-checked={form.feedback_verdict === value}
                  className={form.feedback_verdict === value ? 'active' : ''}
                  onClick={() => setForm((current) => ({ ...current, feedback_verdict: value }))}
                >
                  {label}
                </button>
              ))}
            </div>
          </fieldset>

          <div className="agent-journal-fields">
            <label>
              <span>我的下一步</span>
              <select
                value={form.user_decision}
                onChange={(event) => setForm((current) => ({ ...current, user_decision: event.target.value }))}
              >
                {DECISIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
            </label>
            <label>
              <span>计划复盘日期</span>
              <div className="agent-journal-date">
                <CalendarClock size={15} aria-hidden="true" />
                <input
                  type="date"
                  value={form.planned_review_at}
                  onChange={(event) => setForm((current) => ({ ...current, planned_review_at: event.target.value }))}
                />
              </div>
            </label>
          </div>

          <fieldset>
            <legend>主要依据或问题</legend>
            <div className="agent-journal-reasons">
              {REASONS.map(([value, label]) => (
                <label key={value} className={form.reason_codes.includes(value) ? 'selected' : ''}>
                  <input
                    type="checkbox"
                    checked={form.reason_codes.includes(value)}
                    onChange={() => toggleReason(value)}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <label className="agent-journal-note">
            <span>决策备注 <small>{form.note.length}/500</small></span>
            <textarea
              value={form.note}
              maxLength={500}
              rows={3}
              placeholder="触发条件、待补证据、决策理由"
              onChange={(event) => setForm((current) => ({ ...current, note: event.target.value }))}
            />
          </label>

          <div className="agent-journal-submit">
            <span>{validationError || (dirty ? '有未保存的修改' : '当前内容未变化')}</span>
            <button type="button" onClick={saveFeedback} disabled={Boolean(validationError) || !dirty || saving}>
              {saving ? <span className="agent-inline-spinner" aria-hidden="true" /> : <Save size={16} aria-hidden="true" />}
              {saving ? '保存中' : journal?.latest ? '保存新版本' : '保存决策'}
            </button>
          </div>
        </div>
      )}

      {!canRecord && journal?.latest && (
        <div className="agent-journal-readonly">该日志为只读审阅状态。</div>
      )}

      {error && <div className="agent-journal-message error"><AlertTriangle size={15} aria-hidden="true" />{error}</div>}
      {notice && <div className="agent-journal-message success"><CheckCircle2 size={15} aria-hidden="true" />{notice}</div>}

      {journal?.latest && (
        <div className="agent-journal-history">
          <FeedbackEvent event={journal.latest} latest />
          {journal.items?.length > 1 && (
            <details>
              <summary><History size={15} aria-hidden="true" />查看历史版本（{journal.items.length - 1}）</summary>
              <div>
                {journal.items.slice(0, -1).reverse().map((event) => (
                  <FeedbackEvent event={event} key={event.id} />
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      <p className="agent-journal-policy">{journal?.policy}</p>
    </section>
  )
}
