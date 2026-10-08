/** 起始帧看板的接口形状（后端 server/routers/keyframes.py 的 GET 响应）。 */

export type KeyframeCellStatus = "pending" | "draft" | "approved";

export interface KeyframeCell {
  id: string;
  label: string;
  scene: string;
  /** 站位：谁在哪一侧、坐或站、朝向、服装。 */
  staging: string;
  /** 这一格的起始景别：景别、机位、画面主体。 */
  framing: string;
  characters: string[];
  unit_ids: string[];
  status: KeyframeCellStatus;
  /** 切出的格子图，项目内相对路径；未生成为 null。 */
  image: string | null;
  /** 生成这一格时依据的素材已变化：需重新生成联合图后才能审核。 */
  stale: boolean;
}

/** 联合图当前不能生成的原因：缺哪份素材（场景图或角色图）。 */
export interface KeyframeSheetProblem {
  code: string;
  name: string;
}

export interface KeyframeSheet {
  id: string;
  scene: string;
  /** 联合图整图，项目内相对路径；未生成为 null。 */
  image: string | null;
  problem: KeyframeSheetProblem | null;
  cells: KeyframeCell[];
}

export interface KeyframeUnitRow {
  unit_id: string;
  /** 单元正文开头，供辨认是哪个单元。 */
  text: string;
  cell_id: string | null;
}

/** 项目不是采用 H3 提示词档的参考生视频时，只有这两个字段。 */
export interface KeyframeBoardNotApplicable {
  applicable: false;
  episode: number;
}

export interface KeyframeBoardApplicable {
  applicable: true;
  episode: number;
  has_board: boolean;
  /** 规划之后单元内容或导演说明又变了：看板仍可用，但建议让 Agent 重新规划。 */
  plan_stale: boolean;
  units: KeyframeUnitRow[];
  sheets: KeyframeSheet[];
}

export type KeyframeBoard = KeyframeBoardNotApplicable | KeyframeBoardApplicable;

export interface KeyframeSheetReference {
  /** 「图N为…的参考」：与提示词里的编号一致。 */
  label: string;
  /** 项目内相对路径；参考图在项目之外时为 null。 */
  path: string | null;
}

/** 一张联合图下次生成会发出的内容；素材缺失时只有 problem。 */
export type KeyframeSheetRequest =
  | { sheet_id: string; problem: KeyframeSheetProblem }
  | {
      sheet_id: string;
      problem: null;
      prompt: string;
      aspect_ratio: string;
      references: KeyframeSheetReference[];
    };

export interface KeyframeSheetEnqueueResponse {
  task_id: string;
  deduped: boolean;
  resource_id: string;
}

/** 联合图任务在 tasks-store 里的资源 id：`episode_N/S1`。 */
export function keyframeSheetResourceId(episode: number, sheetId: string): string {
  return `episode_${episode}/${sheetId}`;
}
