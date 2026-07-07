# 実装指示書: ガス拡散解析(浮力考慮・Boussinesq)の追加

状態: **Phase A(定常Boussinesq拡散)実装完了・検証済み**(2026-07-07)。
**Phase C(圧縮性多成分・空力LES→ガス拡散LESリスタート)実装完了・検証済み**(2026-07-07)— 当初計画のPhase B(Boussinesq LES)はPhase Cで置き換え(密度比の制限なし)。

## Phase C 実装概要(rhoReactingBuoyantFoam)
- 完了した空力LES(kOmegaSSTDDES/IDDES)の瞬間場から `rhoReactingBuoyantFoam`(2成分 air/GAS・化学反応オフ)へリスタート。U/k/omega/nut を継承、p/phi は破棄して一様絶対圧から再発達
- GAS分子量 = 密度比 × 28.96(デフォルト水素0.07)。密度は組成から直接計算されるためBoussinesq近似の制限なし
- テンプレート `foam_templates/gasFiles/`。UI: RunタブのDONEなLESケースに「🧪 ガス拡散LESへ移行」
- 実装時に踏んだOpenFOAM側の要求: rhoReactionThermo系(heRhoThermo)必須 / reactionsのelementsは辞書不可 / air初期質量分率の明示 / fvSchemesにfluxRequired p_rgh
- V1制約: ガス段のforceCoeffsなし、実行時に既存postProcessingはpostProcessing_aeroへアーカイブ、ガス段の再実行・延長は未対応

## 背景・目的

現システム(風力解析: 定常kOmegaSST → kOmegaSSTDDES/IDDESへのLESリスタート)を拡張し、**浮力を考慮したガス拡散解析**を追加する。流れ場は定常RANSとLESの両方に対応する。

## 技術方針(調査済みの事実)

- OpenFOAM v2206 に `buoyantBoussinesqSimpleFoam`(定常)/ `buoyantBoussinesqPimpleFoam`(非定常)が存在。チュートリアル `heatTransfer/buoyantBoussinesqSimpleFoam/hotRoom` が場構成(U, p, **p_rgh, T, alphat**, k, ε/ω, nut)・`constant/g`・`transportProperties`(nu, **beta, TRef, Pr, Prt**)のリファレンス
- **温度場Tを規格化濃度Cとして転用**する。Boussinesq浮力項 g·β(T−TRef) はガス濃度による密度差と数学的に同型。ガス密度比 r = ρgas/ρair から **β_eff = 1 − r** を導出(r>1の重いガス→β<0で沈降、r<1→上昇)。TRef=0、Pr→Sc≈1.0、Prt≈0.85
- **適用範囲**: Boussinesq近似は |Δρ/ρ| ≲ 0.2〜0.3 が妥当域。排気ガス・希釈された漏洩ガスはOK。純水素ジェットやLNG重ガス雲の定量評価は圧縮性多成分ソルバーが必要(本指示書のスコープ外、UIに注記を出す)
- 放出源は fvOptions の **`semiImplicitSource`**(v2206 `src/fvOptions/sources/general/` に存在)で T に注入。位置・強度をパラメータ化
- LES: buoyantBoussinesqPimpleFoam + kOmegaSSTDDES/IDDES(LESモデルはソルバー非依存)。**既存の steady→LES リスタート機構をそのまま流用**
- 既存インフラ流用: メッシュ生成・ドメイン自動化・回転・進捗・フェーズ対応結果・断面/動画は全て共通。ただし**ログ名が `log.simpleFoam`/`log.pisoFoam` 決め打ちの箇所**(jobs.py progress、parsers.phase_logs / parse_phase_times、simulations.py sync、Allrunのメモリ監視grep)にソルバー名解決の一般化が必要

## 設計原則

- **DBマイグレーションなし**: 解析種別は `parameters["case_type"]`("aero" デフォルト | "dispersion")で持つ。solver_type(STEADY/UNSTEADY)の意味は不変
- **段階納品**: Phase A = 定常拡散のE2E → 検証 → Phase B = LES拡散+濃度動画+チャットツール
- **aero(風力解析)の既存挙動は一切変えない**(dispersion分岐の追加のみ。ビルド出力はバイト同一を回帰確認)

## Phase A: 定常ガス拡散

### A1. テンプレート `foam_templates/dispersion/`(motorBikeのフォーク)
- `system/controlDict`: application buoyantBoussinesqSimpleFoam。functions は streamLines/cuttingPlane/forceCoeffs を維持し、**cuttingPlane の fields を ( p U T ) に**
- `0.orig/`: 既存の U/p/k/omega/nut に加えて **p_rgh**(壁 fixedFluxPressure、outlet固定)、**T**(inlet=0、壁 zeroGradient)、**alphat**(壁 alphatJayatillekeWallFunction — hotRoom準拠、kOmegaSST用構成)
  - ⚠️ 境界パッチは inlet/outlet/lowerWall/upperWall/frontAndBack/motorBike。**upperWall/frontAndBack は `slip` 型**(symmetryPlane を書くと自動生成メッシュのpatch型と衝突して即クラッシュ — 既知の罠、CLAUDE.md参照)
- `constant/g`: (0 0 -9.81)
- `constant/transportProperties`: nu + **beta/TRef/Pr/Prt**(betaはビルド時上書き)
- `constant/fvOptions`: `semiImplicitSource`、selectionMode points(放出点1点)、injectionRateSuSp で T に注入(強度はビルド時上書き)
- `system/fvSchemes`: div(phi,T) 追加。`system/fvSolution`: p_rgh ソルバー+リラクゼーション(hotRoom準拠、SIMPLE)

### A2. パラメータ(backend/api/simulations.py DEFAULT_PARAMETERS 追加)
```
case_type: "aero"
gas_density_ratio: 1.5       # CO2相当。β_eff = 1 − ratio
source_position: null        # null = ジオメトリ上面中央 [cx, 0, z1] を自動計算
source_rate: 1.0             # 相対強度
```

### A3. case_builder.py の dispersion 分岐
- `build_case()`: `params["case_type"]=="dispersion"` かつ steady のとき DISPERSION_TEMPLATE を使用
- 新ヘルパー `_write_dispersion_props(case_dir, params)`: β=1−gas_density_ratio を transportProperties に、放出点座標(未指定なら回転後STL bboxから自動)と強度を fvOptions に書き込み(`_set_value` 流用)
- Allrun: aero定常と同一構成(`runParallel $(getApplication)` はソルバー名非依存)。ただし**メモリ監視の grep パターン `[s]impleFoam` は `buoyantBoussinesqSimpleFoam`(大文字S)にマッチしない**ため、Allrunテンプレートの監視パターンをソルバー名引数化(steadyは従来文字列のまま=出力不変)

### A4. ログ名解決の一般化(フェーズ対応レイヤー)
`parsers.py` に定数と解決ヘルパーを追加:
```
PHASE1_LOGS = ("log.simpleFoam", "log.buoyantBoussinesqSimpleFoam")
PHASE2_LOGS = ("log.pisoFoam",   "log.buoyantBoussinesqPimpleFoam")
```
「最初に存在するログ」を使うよう以下を変更(既存ケースでは従来と同一の解決結果になること):
- `phase_logs()` / `parse_phase_times()` / `parse_solver_diagnostics()` の優先リスト
- `backend/api/jobs.py job_progress`(steady側ログ名・unsteady側の2ログ)
- `backend/api/simulations.py sync_one_job` の solver_log 判定

### A5. UI(frontend/pages/03_case.py + i18n en/ja 両方)
- 新規ケースフォーム: 解析種別 selectbox(「風力解析(デフォルト)」/「ガス拡散解析」)→ parameters.case_type に保存
- dispersionケースのSetupタブ: ガス設定セクション — プリセット selectbox(CO2 1.53 / メタン 0.55 / 水素 0.07 / カスタム)+密度比・放出点x,y,z・放出強度の number_input、Boussinesq適用範囲の注記(水素等は参考値と明示)
- Visualizationタブの断面 field 選択に「C(濃度)」を追加(field="T" をAPIに渡すだけ — `plot_cutting_plane` はfield名汎用)
- Runタブの乱流モデル表示は既存のまま機能する

### A6. Phase A 検証
- ローカルE2E(実OpenFOAM): boxジオメトリ+dispersionケースで r=0.5(軽) → **プルーム浮上**、r=1.5(重) → **沈降** を両方確認(浮力の符号検証)
- aero回帰: aeroケースのビルド出力がバイト同一、AppTest(種別選択UI、dispersionのガス設定表示)、pytest

## Phase B: LES拡散 + 濃度動画(Phase A完了後)

- `foam_templates/lesFiles_dispersion/`: buoyantBoussinesqPimpleFoam 用 controlDict/fvSchemes(LESスキーム+div(phi,T))/fvSolution(PIMPLE+p_rgh)/turbulenceProperties(kOmegaSSTDDES/IDDES — `lesFiles_kOmegaSST` のdelta構成を踏襲。IDDESは `delta IDDESDelta` 必須)
- `build_les_restart_case()` に dispersion 分岐(ソーステンプレート切替+application名)。リスタートUI・APIのガード(kOmegaSSTの定常のみ)はdispersionもkOmegaSSTベースなのでそのまま
- 動画: cuttingPlane の fields に T が入るので、`render_animation`/APIの field 許可リストに "T" を追加し、UIの動画種類に「断面 C(濃度)」を追加 → ガス雲の非定常挙動の動画
- チャットツール `set_gas_source`(位置・強度・密度比)を agent.py に追加
- 検証: ローカルE2E(定常拡散→LESリスタート→濃度動画)、aero LESリスタート回帰

## 参照する既存実装(流用元)
- テンプレコピー&書換: `case_builder.build_case` / `_set_value` / `_auto_domain_params`
- LESリスタート: `build_les_restart_case` / `_ALLRUN_LES_RESTART`(backend/foam/case_builder.py)
- 断面・動画: `plot_cutting_plane` / `render_animation`(backend/visualization/pyvista_backend.py)— field名は既に汎用
- フェーズ対応: `phase_logs` / `parse_phase_times`(backend/visualization/parsers.py)

## 全体検証(両Phase完了時)
1. `uv run pytest`(新規: ログ名解決ヘルパーのunit test追加)
2. ローカル実OpenFOAM: dispersion定常(浮上/沈降の物理確認)→ LESリスタート → 濃度断面・動画・フェーズ別結果
3. aero回帰: 定常・LESリスタートのビルド出力不変、既存ケースの表示が壊れないこと
4. クラスター実機確認は初回のdispersionジョブ投入時
