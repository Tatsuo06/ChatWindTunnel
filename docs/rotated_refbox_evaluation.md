# snappyHexMesh 精細化領域を `searchableRotatedBox` にする試み — 評価と結論

## 背景・動機

風向は「STLを `Rotation.from_euler("zyx", [-yaw,-pitch,-roll])` で bbox 中心まわりに回転」させて実現している(流れは常に +X)。
snappyHexMesh の精細化領域 `refinementBox` は**回転後 STL の軸平行バウンディングボックス(AABB)**で、
yaw/pitch/roll を付けると車体が傾くため AABB が実体より大きく膨らみ、四隅の「空の流体」まで精細化される。

そこで、**AABB の代わりに `searchableRotatedBox`(傾いた箱)**で車体の向きにピッタリ追従させ、
無駄な精細化セルを減らせないかを検証した(OpenFOAM v2206 が正式サポート:`type searchableRotatedBox; span; origin; e1; e3;`)。

- 箱の定義: 回転前 STL のタイト箱を、STL と同じ回転で傾ける。`origin`=bbox中心(=回転中心)、`e1=R[:,0]`, `e3=R[:,2]`、
  `span`=回転前寸法+**per-axis** パディング `0.2·各軸`(narrow 方向を細く保つのが肝)。

## 検証結果

### 1. 理想化テスト(薄板 2.0×0.1×0.5, yaw 45°, refinement 3–4)

| | セル数 | checkMesh | 最大スキュー |
|---|---|---|---|
| 回転箱 | **237,663** | Mesh OK | 0.79 |
| AABB | 677,472 | Mesh OK | 0.79 |

→ **約65%削減・品質同等**。ここだけ見ると効果大に見えた。

### 2. 実ケース(m5480, yaw 40°, nx=80, refinement 5–6) — case23(AABB) vs case184(回転箱)

| | セル数 | 最大スキュー | refinementBox 体積 |
|---|---|---|---|
| case23(AABB) | 1,827,640 | 6.218 | 1.84 m³ |
| case184(回転箱) | **1,805,170** | 6.218 | 0.35 m³ |

- **セル数削減はわずか約1.2%(22,470セル)**。箱の体積は 5.2 倍小さくなったのに、総数はほぼ変わらない。
- 力係数(反復1000)も**約1%以内で一致**: Cd 1.5419 vs 1.5485(+0.43%)、Cl 1.7808 vs 1.8042(+1.31%)、Cs 0.4135 vs 0.4084。
- checkMesh 品質は同一(スキュー 6.218)。回転箱による悪化なし。

## なぜ実ケースでは効かないか

`refinementBox` は **level 4**、サーフェス精細化は **level 5–6**。総セル数(180万)は
**geometry 表面まわりの level 5–6 の細かいシェルが支配的**で、これは AABB でも回転箱でも同一。
回転箱が縮めるのは **level 4 の体積領域**にすぎず、総数に占める割合が小さい。
理想化テスト(表面が粗く、体積 refinement が支配的)でだけ大きな効果が出ていた。

## 結論(2026-07-09)

- 回転箱は**正しく動作し、結果も一致し、害もない**が、**利用者の通常設定(表面精細化 5–6 が支配的)では削減効果は約1%**にとどまる。
- 実装の複雑化(snappy dict 分岐、プレビューの傾き箱描画、3D プレビューの回転頂点)に見合わないと判断し、**revert して従来の AABB (`type box`) に戻した**。
- もし将来メッシュ削減を狙うなら、回転箱より **refinementBox のレベル自体の見直し**(過剰な近傍 refinement の削減)の方が効く見込み。

## 実装した/戻した箇所(参考)

`case_builder.py`(`_rotated_refbox_params`, `_write_snappy_hex_mesh` の分岐, `build_case` の分岐)、
`visualization/pyvista_backend.py`(`preview_geometry` の傾き箱描画)、
`api/results.py`(PNG プレビュー/`geometry-3d-data` の回転頂点)、
`frontend/pages/03_case.py`(`_box_trace_corners` と 3D プレビュー分岐)、CLAUDE.md。いずれも revert 済み。
