import json, os, random, urllib.request, urllib.error
from flask import Flask, jsonify, render_template, request, session

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'CHANGE_ME_FOR_PRODUCTION')

BASE = json.load(open(os.path.join(os.path.dirname(__file__), 'questions.json'), encoding='utf-8'))

def shuffled_fallback():
    items = random.sample(BASE, len(BASE))
    output = []
    for q in items:
        order = list(range(4)); random.shuffle(order)
        output.append({'q': q['q'], 'o': [q['o'][i] for i in order], 'a': order.index(q['a']), 'e': q['e']})
    return output

def generate_ai():
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key:
        return None
    prompt = ('Составь ровно 30 НОВЫХ разнообразных заданий МОДО по математической грамотности '
              'для 4 класса Казахстана, на русском языке. Темы: многозначные числа, действия, '
              'порядок действий, уравнения, текстовые задачи, движение, величины, геометрия, '
              'доли и дроби. У каждого 4 РАЗНЫХ правдоподобных варианта, ровно один верный. '
              'Проверяй вычисления и давай краткое решение. Ответ только JSON-объект вида '
              '{"questions":[{"q":"текст","o":["A","B","C","D"],"a":0,"e":"решение"},...]}. '
              'a — индекс правильного ответа от 0 до 3. Генерируй новые числа и сюжеты. '
              'Случайный идентификатор варианта: ' + str(random.SystemRandom().randrange(10**12)))
    body = json.dumps({'model': os.environ.get('OPENAI_MODEL','gpt-4o-mini'),
                       'messages':[{'role':'user','content':prompt}],
                       'response_format':{'type':'json_object'}, 'temperature':0.9}, ensure_ascii=False).encode()
    req = urllib.request.Request('https://api.openai.com/v1/chat/completions', data=body,
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=100) as resp:
        data = json.load(resp)
    items = json.loads(data['choices'][0]['message']['content'])['questions']
    if len(items)!=30: raise ValueError('ИИ вернул не 30 заданий')
    for q in items:
        if not isinstance(q['q'],str) or not isinstance(q['e'],str) or len(q['o'])!=4 or len(set(q['o']))!=4 or type(q['a'])!=int or not 0<=q['a']<4:
            raise ValueError('Неверный формат задания ИИ')
    return items

@app.get('/')
def home():
    return render_template('index.html')

@app.get('/api/questions')
def questions():
    try:
        items = generate_ai()
        source = 'ИИ' if items else 'Банк вопросов (нет ключа ИИ)'
    except Exception as exc:
        app.logger.warning('AI generation failed: %s', exc)
        items = None
        source = 'Банк вопросов (ИИ недоступен)'
    if items is None: items = shuffled_fallback()
    # Session stores answers server-side in signed cookie: never send correct keys to client.
    # Cookie size is limited, so use a short quiz ID and server-side storage for answers.
    import uuid
    quiz_id = uuid.uuid4().hex
    QUIZZES[quiz_id] = items
    if len(QUIZZES)>500:
        QUIZZES.pop(next(iter(QUIZZES)))
    return jsonify({'quiz_id':quiz_id, 'source':source,
                    'questions':[{'q':q['q'],'o':q['o']} for q in items]})

QUIZZES = {}

@app.post('/api/submit')
def submit():
    data = request.get_json(silent=True) or {}
    name = str(data.get('name','')).strip()[:120]
    school_class = str(data.get('school_class','')).strip()[:30]
    quiz_id = str(data.get('quiz_id',''))
    answers = data.get('answers')
    if not name or not school_class: return jsonify({'error':'Укажите ФИО и класс'}),400
    items = QUIZZES.pop(quiz_id, None)
    if items is None: return jsonify({'error':'Тест не найден или уже отправлен. Обновите страницу.'}),400
    if not isinstance(answers,list) or len(answers)!=len(items):
        return jsonify({'error':'Неверный формат ответов'}),400
    score = sum(a == q['a'] for a,q in zip(answers,items))
    percent = round(score/len(items)*100,1)
    review = [{'q':q['q'], 'selected':q['o'][a] if type(a)==int and 0<=a<4 else 'Нет ответа',
               'correct':q['o'][q['a']], 'ok':a==q['a'], 'explanation':q['e']}
              for a,q in zip(answers,items)]
    lines = [f'Ученик: {name}',f'Класс: {school_class}',f'Результат: {score}/{len(items)} ({percent}%)','']
    for i,r in enumerate(review,1):
        lines += [f'{i}. {r["q"]}',f'Ответ: {r["selected"]}',f'Правильно: {r["correct"]}',
                  f'Статус: {"Верно" if r["ok"] else "Ошибка"}',f'Разбор: {r["explanation"]}','']
    sent, error = send_email(name, {'school_class': school_class, 'score': score, 'percent': percent, 'wrong': len(items)-score, 'details': '\n'.join(lines)})
    return jsonify({'score':score,'total':len(items),'percent':percent,'review':review,
                    'email_sent':sent,'email_error':error})

def send_email(name, body):
    """Send through EmailJS using the already configured template variables."""
    service = os.environ.get('EMAILJS_SERVICE_ID', 'service_eqm6vxw').strip()
    template = os.environ.get('EMAILJS_TEMPLATE_ID', 'template_oxirbqr').strip()
    public = os.environ.get('EMAILJS_PUBLIC_KEY', 'oXqaExjsVGKYeodsm').strip()
    recipient = os.environ.get('RESULT_EMAIL', 'charming_nastena@mail.ru').strip()
    # EmailJS REST endpoint expects user_id (public key), service_id and template_id.
    # Recipient is fixed in the EmailJS template, not taken from the student.
    payload = {
        'service_id': service, 'template_id': template, 'user_id': public,
        'template_params': {
            'student_name': name,
            'student_class': body['school_class'],
            'correct': str(body['score']),
            'percent': str(body['percent']),
            'wrong': str(body['wrong']),
            'details': body['details'],
            'to_email': recipient,
        },
    }
    req = urllib.request.Request('https://api.emailjs.com/api/v1.0/email/send',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type':'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            if response.status not in (200, 201, 202):
                return False, 'EmailJS ответил кодом '+str(response.status)
        return True, ''
    except urllib.error.HTTPError as exc:
        detail = exc.read(350).decode('utf-8', errors='replace')
        app.logger.warning('EmailJS error: %s %s', exc.code, detail)
        return False, f'EmailJS HTTP {exc.code}: {detail}'
    except Exception as exc:
        app.logger.exception('EmailJS delivery failed')
        return False, 'Ошибка соединения с EmailJS: '+type(exc).__name__

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT','5000')))
