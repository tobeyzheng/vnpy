import importlib.util
spec = importlib.util.find_spec('futu')
print('FOUND' if spec else 'MISSING')
