import requests
url = 'http://host.docker.internal:9000/v2/models/nv-embed-v2/infer'
texts = ['This is a test query']
data = {
    'inputs': [
        {'name': 'query', 'shape': [len(texts)], 'datatype': 'BYTES', 'data': texts},
        {'name': 'documents', 'shape': [len(texts), 1], 'datatype': 'BYTES', 'data': [[t] for t in texts]}
    ]
}
res = requests.post(url, json=data)
print('Status:', res.status_code)
print('Response:', res.text[:500])
