"""Human-readable review intervals, kept out of the rendered video."""
from html import escape


REASONS = {
    'weak_detection': 'Невысокая уверенность распознавания; проверьте выбранный объект.',
    'no_subject': 'Главный объект не обнаружен; сохранён полный кадр.',
    'unstable_subject': 'Нет устойчивого главного объекта; сохранён полный кадр.',
    'subject_lost': 'Объект временно потерян; положение рамки оценено по соседним кадрам.',
    'multiple_subjects': 'Несколько важных кандидатов не помещаются вместе; выбран один.',
    'partial_subject': 'Объект показан частично: проверьте лицо, руки и действие.',
    'outside_action': 'Заметное движение осталось за рамкой.',
    'fast_pan': 'Быстрое перемещение рамки; проверьте плавность.',
}


def intervals(plan, duration):
    result = []
    for i, key in enumerate(plan.keyframes):
        end = min(duration, plan.keyframes[i+1].time if i+1 < len(plan.keyframes) else duration)
        flags = sorted(set(key.reason.split('|')) & REASONS.keys())
        if end <= key.time or not flags:
            continue
        if (result and result[-1]['scene'] == key.scene and result[-1]['reasons'] == flags
                and abs(result[-1]['end']-key.time) < 1e-6 and result[-1]['mode'] == key.mode):
            result[-1]['end'] = end
        else:
            result.append(dict(start=key.time, end=end, scene=key.scene, mode=key.mode, reasons=flags))
    return result


def timecode(seconds):
    milliseconds = round(seconds * 1000)
    minutes, tail = divmod(milliseconds, 60000)
    sec, ms = divmod(tail, 1000)
    return f'{minutes:02d}:{sec:02d}.{ms:03d}'


def html_report(destination, entries):
    rows = ''.join(f'<tr><td>{timecode(e["start"])} — {timecode(e["end"])}</td>'
                   f'<td>{e["scene"]+1}</td><td>{"Кроп" if e["mode"] == "crop" else "Полный кадр"}</td>'
                   f'<td>{escape(" ".join(REASONS[r] for r in e["reasons"]))}</td></tr>' for e in entries)
    return ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Reframe — проверка монтажа</title>'
            '<style>body{font:16px system-ui;background:#151719;color:#eee;margin:32px;line-height:1.5}'
            'table{border-collapse:collapse;width:100%}td,th{padding:12px;border:1px solid #555;text-align:left}'
            'td:first-child{white-space:nowrap}</style><h1>Участки для проверки</h1>'
            f'<p>Видео: {escape(destination.name)}</p><p>Интервалов: {len(entries)}. '
            'Таймкоды отсчитываются от начала результата, конец интервала не включён.</p>'
            '<p>Это автоматические сигналы, а не полный список ошибок. Распознавание текста, '
            'говорящего и смысла сцены пока не реализовано. Даже при пустом списке проверьте '
            'надписи, логотипы, лица, взаимодействия и плавность.</p>'
            '<table><tr><th>Интервал</th><th>Сцена</th><th>Режим</th><th>Что проверить</th></tr>'
            + rows + '</table></html>')
