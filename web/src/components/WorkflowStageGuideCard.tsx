import type { Job } from '../types'

type Props = {
  job: Job
  step: string
  onGo: (step: string) => void
}

type PageGuide = {
  stageLabel: string
  task: string
  detail: string
  nextLabel: string
  targetStep?: string
}

function pageGuide(step: string, job: Job): PageGuide {
  if (step === 'goals') {
    return {
      stageLabel: '选择底稿目标',
      task: '请选择本次审阅使用的底稿目标，并填写被审计单位与期间截止日。',
      detail: '确认目标后，系统将进入抽样清单上传页面。',
      nextLabel: '上传抽样清单',
    }
  }
  if (step === 'sample_upload') {
    return {
      stageLabel: '上传抽样清单',
      task: '上传并校验本次审阅的抽样清单，系统将按清单建立待审业务。',
      detail: '清单校验通过后，进入总工作台查看全部样本状态。',
      nextLabel: '总工作台',
      targetStep: Number(job.sample_population?.count || 0) > 0 ? 'sample_desk' : undefined,
    }
  }
  if (step === 'sample_desk') {
    return {
      stageLabel: '总工作台',
      task: '查看全部抽样业务的处理状态、缺失资料和需要人工判断的事项。',
      detail: '按工作台提示处理异常后，为抽样业务补充凭证资料。',
      nextLabel: '上传凭证',
      targetStep: 'upload_ocr:upload',
    }
  }
  if (step === 'upload_ocr') {
    return {
      stageLabel: '上传凭证',
      task: '按抽样清单中的业务上传凭证或混装资料包，并执行识别。',
      detail: '识别完成后，仅对缺失或存疑字段进行人工核对。',
      nextLabel: '核对字段',
      targetStep: 'field_confirm',
    }
  }
  if (step === 'field_confirm' || step === 'evidence_match' || step === 'relations_gate4') {
    return {
      stageLabel: '核对字段',
      task: '对照原始凭证补充缺失字段，并确认实际修改后的字段内容。',
      detail: '字段核对完成后，进入审阅结论确认。',
      nextLabel: '确认结论',
      targetStep: 'conclusion_gate5',
    }
  }
  if (step === 'conclusion_gate5') {
    return {
      stageLabel: '确认结论',
      task: '复核测试异常与审计判断，确认每笔业务的最终审阅结论。',
      detail: '全部样本完成确认后，生成并导出审阅底稿。',
      nextLabel: '导出底稿',
      targetStep: 'workbook_export',
    }
  }
  return {
    stageLabel: '导出底稿',
    task: '检查底稿范围与审阅结果，生成并下载最终审阅底稿。',
    detail: '导出后请留存文件，并确认本轮抽样审阅已完成。',
    nextLabel: '完成本轮审阅',
  }
}

export function WorkflowStageGuideCard({ job, step, onGo }: Props) {
  const guide = pageGuide(step, job)

  return (
    <section className="desk-next-action workflow-stage-guide" aria-label="当前阶段指引">
      <div className="desk-next-copy">
        <span className="desk-next-kicker">当前流程指引</span>
        <strong>当前阶段：{guide.stageLabel}</strong>
        <span>{guide.task}</span>
        <span className="hint">{guide.detail}</span>
      </div>
      <div className="desk-next-controls">
        {guide.targetStep ? (
          <button
            type="button"
            className="btn primary desk-next-cta"
            onClick={() => onGo(guide.targetStep!)}
          >
            下一步：{guide.nextLabel}
          </button>
        ) : (
          <div className="goals-next-stage">
            <span>下一步：</span>
            <strong>{guide.nextLabel}</strong>
          </div>
        )}
      </div>
    </section>
  )
}
