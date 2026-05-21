# ChatWindTunnel 要件定義書

**バージョン**: 0.1  
**作成日**: 2026-05-20  
**ステータス**: 確定（コーディング開始前）

---

## 1. システム概要

OpenFOAM v2206 を使った風洞シミュレーションをチャットベースで設定・実行・可視化するWebアプリケーション。  
CADファイルをアップロードし、風速・風向などを自然言語で設定して計算を投入、結果を整理・表示する。

---

## 2. システムアーキテクチャ

```
[Streamlit UI]  ←HTTP→  [FastAPI Backend]
                               │
                  ┌────────────┼────────────┐
                  │            │            │
             [foamlib]   [PostgreSQL]  [LM Studio]
             ケース生成   プロジェクト管理  ローカルLLM
                  │
        ┌─────────┴──────────┐
   [Local OpenFOAM]     [Cluster SSH]
   /Applications/...    192.168.20.2 (PBS)
   テスト用               本番・並列計算
```

### コンポーネント方針
- **Streamlit**: UIレイヤー専用。ビジネスロジックは持たない
- **FastAPI**: 認証・DB操作・ジョブ管理・LLM呼び出しを担当
- **可視化モジュール**: `visualization/` ディレクトリに分離し、将来的な高機能化（dash-vtk等）に備える

---

## 3. 技術スタック

| コンポーネント | 技術 | 補足 |
|---|---|---|
| UI | Streamlit | |
| API / ビジネスロジック | FastAPI (Python) | |
| OpenFOAMケース管理 | foamlib | |
| CAD変換・回転 | cadquery | STEP/IGES→STL変換、STL回転 |
| LLM（ローカル） | LM Studio | OpenAI互換API (localhost:1234) |
| LLM抽象化 | LiteLLM | 将来的にClaude API / OpenAI に切替可能 |
| DB | PostgreSQL + SQLAlchemy | |
| 認証 | FastAPI JWT + streamlit-authenticator | |
| ジョブ実行（ローカル） | subprocess | テスト用 |
| ジョブ実行（クラスタ） | paramiko SSH + qsub (PBS/Torque) | 本番用 |
| ファイル転送 | NFS（第1候補）/ SCP paramiko（フォールバック） | 設定で切替 |
| 2D可視化 | plotly | 残差・係数グラフ |
| 3D可視化 | PyVista → サーバーサイドPNG | 将来的に dash-vtk 等へ換装可能 |

---

## 4. ユーザー管理・認証

| 区分 | 権限 |
|------|------|
| 管理者 (admin) | ユーザー追加・削除、全プロジェクト閲覧・削除、システム設定変更 |
| 一般ユーザー (user) | 自分のプロジェクト作成・実行・閲覧 |

- JWT認証（FastAPI側）+ streamlit-authenticator（UI側）
- パスワードはbcryptハッシュ化して保存

---

## 5. データベース設計

```
users
  id, username, hashed_password, role (admin|user), created_at

projects
  id, name, description, owner_id → users.id, created_at

simulations
  id
  project_id → projects.id
  solver_type: STEADY | UNSTEADY
  status: PENDING | MESHING | RUNNING | DONE | FAILED
  job_id          # クラスタのPBSジョブID
  cad_file_path   # アップロード元ファイル
  stl_file_path   # 変換・回転済みSTL
  case_dir        # OpenFOAMケースディレクトリ
  parameters      # JSON: 全設定値
  yaw_deg         # 風向ヨー角 [deg]
  pitch_deg       # 風向ピッチ角 [deg]
  result_path     # 結果ディレクトリ
  log_path        # 実行ログ
  created_at, started_at, finished_at
```

---

## 6. ソルバー・テンプレート

### 6.1 定常解析（simpleFoam / kOmegaSST RANS）

**テンプレート**: `foam_templates/motorBike/`

| 設定項目 | OpenFOAMパラメータ | デフォルト値 |
|---|---|---|
| 風速 [m/s] | `flowVelocity` の大きさ | 20 m/s |
| 乱流運動エネルギー k | `turbulentKE` | 0.24 m²/s² |
| 乱流比散逸率 ω | `turbulentOmega` | 1.78 s⁻¹ |
| 反復数 | `controlDict.endTime` | 500 |
| 並列数 | `decomposeParDict.numberOfSubdomains` | 6 |
| メッシュ精細度 | `snappyHexMeshDict` refinement level | (5, 6) |
| 参照面積 Aref | `forceCoeffs.Aref` | 0.75 m² |
| 代表長さ lRef | `forceCoeffs.lRef` | 1.42 m |
| 力の中心 CofR | `forceCoeffs.CofR` | (0.72, 0, 0) m |

**実行ステップ**:
```
surfaceFeatureExtract → blockMesh → decomposePar →
snappyHexMesh → topoSet → potentialFoam → simpleFoam →
reconstructParMesh → reconstructPar
```

### 6.2 非定常解析（pisoFoam / SpalartAllmarasDDES LES）

**テンプレート**: `foam_templates/motorBike_LES/`

定常パラメータに加えて：

| 設定項目 | OpenFOAMパラメータ | デフォルト値 |
|---|---|---|
| 物理終了時刻 | `controlDict.endTime` (LES) | 0.7 s |
| タイムステップ | `controlDict.deltaT` (LES) | 1×10⁻⁴ s |

**実行フロー**:
```
フェーズ1: SpalartAllmaras RAS / simpleFoam で500反復収束
    ↓
フェーズ2: processor*/500 → processor*/0 にコピー
           turbulenceProperties / controlDict / fvSchemes / fvSolution を LES用に差替
           pisoFoam で非定常計算
    ↓
reconstructParMesh → reconstructPar
```

---

## 7. 風向設定の実装方針

流入条件・計算領域（blockMesh）はテンプレートのまま固定し、STLジオメトリを回転させることで任意風向を実現する。

```
ユーザー入力: ヨー角 α [deg]、ピッチ角 β [deg]
    ↓
STLを (-α, -β) 回転 (cadquery または numpy-stl + scipy.spatial.transform)
    ↓
rotated.stl を constant/triSurface/ に配置
    ↓
OpenFOAM 実行（流入は常にX方向）
    ↓
後処理: Cd/Cl/Cm を回転行列で物体座標系に変換して出力
```

**風洞寸法**（両テンプレート共通）:  
X: -5 〜 +15 m、Y: -4 〜 +4 m、Z: 0 〜 +8 m

---

## 8. CADファイル処理

```
アップロード（ドラッグ&ドロップ）
    ├── .stl                → そのまま使用
    └── .step / .iges / .obj → cadquery で STL変換
         ↓
バウンディングボックスを表示してユーザーが向きを確認
         ↓
ヨー・ピッチ角を入力 → STL回転
         ↓
constant/triSurface/<geometry>.stl に配置
         ↓
surfaceFeatureExtract で .eMesh 生成 → snappyHexMesh
```

---

## 9. ジョブ実行環境

### 9.1 ローカル（Mac・テスト用）
- OpenFOAM: `/Applications/OpenFOAM-v2206.app`（volume mount方式）
- 実行: subprocess

### 9.2 クラスタ（本番）
- ログインノード: `192.168.20.2`
- ジョブスケジューラ: PBS/Torque（qsub）
- リソース: `nodes=1:ppn=16`（16コア並列）

**PBSジョブスクリプトテンプレート** (`sample_qsub.sh` 参照):
```bash
#!/bin/bash
#PBS -l nodes=1:ppn=16
#PBS -o log.job
#PBS -N <job_name>
NCPU=`wc -l < $PBS_NODEFILE`
cd $PBS_O_WORKDIR
echo $PBS_O_WORKDIR $NCPU
# OpenFOAM実行コマンドをここに追記
```

**ジョブ監視**: SSH経由で `qstat -j <job_id>` をポーリング

### 9.3 ファイル転送抽象化

```python
class FileTransport(ABC):
    def upload(self, local_path: Path, remote_path: str) -> None: ...
    def download(self, remote_path: str, local_path: Path) -> None: ...

class NFSTransport(FileTransport):   # NFS共有確定後に使用
    ...

class SCPTransport(FileTransport):   # paramiko SCP（フォールバック）
    ...
```

環境変数 `TRANSPORT_MODE=nfs|scp` で切替。

---

## 10. チャット（LLM）設計

### LLM接続
- **現在**: LM Studio（OpenAI互換API: `http://localhost:1234/v1`、モデル: `google/gemma-4-e4b`）
- **将来**: LiteLLM経由でClaude API / OpenAI GPT-4oへの切替を設定のみで対応

### チャットの役割
1. **パラメータ設定**: 自然言語入力をOpenFOAMパラメータに変換（Tool Use）
2. **結果解釈**: Cd/Cl/残差収束結果を説明・考察

### Tool Use（Function Calling）定義

```python
tools = [
    set_flow_conditions(velocity_mps, yaw_deg, pitch_deg, turbulence_intensity),
    set_solver_settings(solver_type, end_time, delta_t, n_processors),
    set_mesh_settings(refinement_min, refinement_max),
    set_force_reference(aref, lref, cofr),
    get_result_summary(simulation_id),   # Cd/Cl/Cmと収束状況を返す
    interpret_results(simulation_id),    # LLMが結果を解釈して説明
]
```

---

## 11. 可視化設計

可視化モジュールは `visualization/` に分離し、バックエンド実装を差し替え可能にする。

```python
class VisualizationBackend(ABC):
    def plot_residuals(self, log_path) -> bytes:         # PNG
    def plot_force_coefficients(self, data_path) -> bytes:  # PNG
    def plot_cutting_plane(self, vtk_path) -> bytes:     # PNG
    def plot_streamlines(self, vtk_path) -> bytes:       # PNG
    def preview_geometry(self, stl_path) -> bytes:       # PNG

class PyVistaBackend(VisualizationBackend):  # 現在の実装
    ...

# 将来: class DashVTKBackend(VisualizationBackend): ...
```

### 表示する可視化項目

| 出力 | データソース | 現在の実装 |
|------|-----------|---------|
| 残差収束履歴 | `log.simpleFoam` / `log.pisoFoam` | plotly |
| Cd/Cl/Cm 時系列 | `postProcessing/forceCoeffs1/*/coefficient.dat` | plotly |
| 圧力・速度 断面図（y=0） | `postProcessing/cuttingPlane/*/yNormal.vtp` | PyVista → PNG |
| ストリームライン | `postProcessing/streamLines/*/streamLines.vtk` | PyVista → PNG |
| CADジオメトリ確認 | STLファイル | PyVista → PNG |

---

## 12. ディレクトリ構成（予定）

```
ChatWindTunnel/
├── docs/
│   └── requirements.md          # 本文書
├── foam_templates/
│   ├── motorBike/               # 定常解析テンプレート
│   └── motorBike_LES/           # 非定常解析テンプレート
├── sample_qsub.sh               # PBSジョブスクリプト参考
├── backend/
│   ├── main.py                  # FastAPI エントリポイント
│   ├── api/
│   │   ├── auth.py
│   │   ├── projects.py
│   │   ├── simulations.py
│   │   └── chat.py
│   ├── core/
│   │   ├── config.py            # 環境変数・設定
│   │   └── security.py          # JWT
│   ├── db/
│   │   ├── models.py
│   │   └── session.py
│   ├── foam/
│   │   ├── case_builder.py      # テンプレートからケース生成 (foamlib)
│   │   ├── steady.py            # simpleFoam設定
│   │   └── unsteady.py          # pisoFoam/LES設定
│   ├── cad/
│   │   └── converter.py         # CAD変換・STL回転 (cadquery)
│   ├── cluster/
│   │   ├── base.py              # 抽象クラス
│   │   ├── local_runner.py      # subprocess実行
│   │   └── cluster_runner.py    # SSH + qsub
│   ├── transfer/
│   │   ├── base.py              # FileTransport抽象クラス
│   │   ├── nfs.py
│   │   └── scp.py
│   ├── chat/
│   │   └── agent.py             # LiteLLM + Tool Use
│   └── visualization/
│       ├── base.py              # VisualizationBackend抽象クラス
│       ├── pyvista_backend.py   # PyVista実装
│       └── parsers.py           # OpenFOAMログ・結果パーサ
└── frontend/
    ├── app.py                   # Streamlit エントリポイント
    └── pages/
        ├── 01_projects.py
        ├── 02_simulation.py
        └── 03_results.py
```

---

## 13. 未決定事項

| 項目 | 状況 | 対応方針 |
|------|------|---------|
| クラスタのNFS共有 | 未確認 | NFSとSCP両方実装、設定切替で対応 |
| クラスタのqsubリソース詳細 | `sample_qsub.sh` のみ判明 | 詳細確認後にジョブスクリプトテンプレート完成 |
| クラスタのOpenFOAMパス | 未確認 | 環境変数 `FOAM_CLUSTER_INSTALL` で設定 |
| LM Studioのモデル名 | `google/gemma-4-e4b` | 設定ファイルで変更可能 |

---

## 14. 開発フェーズ

| フェーズ | 内容 |
|---------|------|
| Phase 1 | DB・認証・プロジェクト管理 (FastAPI + PostgreSQL) |
| Phase 2 | CAD変換・OpenFOAMケース生成 (cadquery + foamlib) |
| Phase 3 | ローカル実行・結果可視化 (subprocess + PyVista) |
| Phase 4 | Streamlit UI |
| Phase 5 | チャット (LiteLLM + Tool Use) |
| Phase 6 | クラスタ対応 (SSH + qsub) |
| Phase 7 | テスト・整合確認 |
