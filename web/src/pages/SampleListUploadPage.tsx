import { useRef, useState } from 'react'

import { api } from '../api'
import type { Job } from '../types'

type Props = {
  job: Job
  onJob: (job: Job) => void
  onGo: (step: string) => void
}

export function SampleListUploadPage({ job, onJob, onGo }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const sampleCount = Number(job.sample_population?.count || 0)

  const importSampleList = async (file: File) => {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const next = await api.importSampleExcel(job.job_id, file)
      onJob(next)
      const count = Number(next.sample_population?.count || 0)
      setMessage(`抽样清单校验通过，已生成 ${count} 笔业务。`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <div className="panel panel-fill sample-upload-page">
      <div className="panel-head">
        <div>
          <h3>上传抽样清单</h3>
          <div className="hint">
            这里只上传并校验抽样清单。期间截止日沿用“选择底稿目标”中的项目参数，无需重复填写。
          </div>
        </div>
        {sampleCount > 0 && (
          <button type="button" className="btn primary" onClick={() => onGo('sample_desk')}>
            进入总工作台
          </button>
        )}
      </div>

      <div className="panel-body sample-upload-body">
        <section className="sample-upload-card" aria-labelledby="sample-upload-title">
          <div>
            <span className="kicker">SAMPLE LIST</span>
            <h4 id="sample-upload-title">上传抽样清单</h4>
            <p className="hint">
              支持 .xlsx 和 .xlsm。系统将校验必要列、业务编号唯一性和有效数据行。
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xlsm"
            hidden
            aria-label="选择抽样清单文件"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void importSampleList(file)
            }}
          />
          <button
            type="button"
            className={`btn${sampleCount > 0 ? '' : ' primary'}`}
            disabled={busy}
            onClick={() => inputRef.current?.click()}
          >
            {busy ? '校验中…' : sampleCount > 0 ? '更换抽样清单' : '上传抽样清单'}
          </button>
        </section>

        {sampleCount > 0 && (
          <div className="sample-upload-result" role="status">
            <strong>当前清单已就绪</strong>
            <span>{sampleCount} 笔业务</span>
            {job.sample_population?.source && <span>{job.sample_population.source}</span>}
          </div>
        )}
        {message && <p className="ok-text">{message}</p>}
        {error && <p className="err" role="alert">{error}</p>}
      </div>
    </div>
  )
}
