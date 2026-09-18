"""Japanese UI strings. Source strings in code are English git wording; this
dictionary is registered with bpy.app.translations and applies when Blender's
interface language is Japanese."""
import bpy

_JA = {
    # panel / tab
    "Object Commits": "オブジェクトコミット",
    "Commits": "コミット",
    "No active object": "アクティブオブジェクトなし",
    # operators (labels + descriptions)
    "Commit": "コミット",
    "Store a restore point for the active object":
        "アクティブオブジェクトの復元ポイントを記録する",
    "Checkout": "チェックアウト",
    "Overwrite the object with the selected commit; the working state is "
    "stashed first if it differs from HEAD":
        "選択中のコミットでオブジェクトを上書きする。作業状態が HEAD と違えば"
        "先に自動スタッシュする",
    "Stash": "スタッシュ",
    "Store the working state without moving HEAD":
        "HEAD を動かさずに作業状態を一時保存する",
    "Pop": "ポップ",
    "Restore the latest stash and remove it":
        "最新のスタッシュを復元して消す",
    "Delete Commit": "コミットを削除",
    "Delete the selected commit (later commits link to the previous one)":
        "選択中のコミットを削除する (後続のコミットは前のコミットにつながる)",
    "Browse": "閲覧",
    "Exit Browse": "閲覧を終了",
    "Show the selected commit in the viewport instead of the working "
    "object. Selecting in the list also enters this mode; a click in the "
    "viewport or Esc leaves it":
        "作業中のオブジェクトの代わりに選択中のコミットをビューポートに表示する。"
        "リストを選択しても入る。ビューポートのクリックか Esc で抜ける",
    "Verify": "検証",
    "Check every commit's geometry against its stored hash":
        "全コミットの形状データをハッシュで照合する",
    "Diff vs HEAD": "HEAD との差分",
    "Compare the working object with HEAD (computes a hash)":
        "作業中の状態を HEAD と比較する (ハッシュ計算あり)",
    "Message for the next commit": "次のコミットに付けるメッセージ",
    "No commit selected": "コミットが選択されていません",
    "%d commits verified": "コミット %d 件すべて正常",
    # state box
    "HEAD #%d": "HEAD #%d",
    "HEAD: none": "HEAD なし",
    "modified": "変更あり",
    "clean": "変更なし",
    "Browsing: the viewport shows the selected commit":
        "閲覧中: ビューポートは選択中のコミットを表示",
    "Linked / override: checkout disabled":
        "リンク / オーバーライド: チェックアウト不可",
    "Data shared by %d objects; checkout makes this one single-user":
        "データを %d オブジェクトで共有中。チェックアウト時にこのオブジェクトだけ分離",
    # list
    "Tag": "タグ",
    "[auto]": "[自動]",
    "[stash]": "[スタッシュ]",
    "Diff #%d vs %s": "#%d と %s の差分",
    "root": "初回",
    "%d commits, %d snapshots, %s verts": "コミット %d 件 / スナップショット %d 件 / 頂点 %s",
    # default messages
    "stash": "スタッシュ",
    "commit %d": "コミット %d",
    "auto stash before checkout #%d": "#%d チェックアウト前の自動スタッシュ",
    # diff lines
    "(first commit)": "(初回のコミット)",
    "no tracked changes": "追跡対象に変更なし",
    "geometry edited (same counts)": "形状を編集 (頂点数は同じ)",
    "transform changed": "位置・回転・拡縮が変化",
    "material slots changed": "マテリアルスロットが変化",
    "custom properties changed": "カスタムプロパティが変化",
    "order changed": "順序が変化",
    # errors
    "linked / overridden object cannot be checked out":
        "リンク / オーバーライドのオブジェクトはチェックアウトできません",
    "no stash": "スタッシュがありません",
    "snapshot data type differs from object data":
        "スナップショットの形状データの種類がオブジェクトと異なります",
    "selected commit has no geometry to browse":
        "選択中のコミットには表示できる形状がありません",
}

_CONTEXTS = ("*", "Operator")

translations_dict = {
    "ja_JP": {(ctx, k): v for k, v in _JA.items() for ctx in _CONTEXTS},
}


def register():
    try:
        bpy.app.translations.register(__name__, translations_dict)
    except ValueError:
        pass


def unregister():
    try:
        bpy.app.translations.unregister(__name__)
    except Exception:
        pass
