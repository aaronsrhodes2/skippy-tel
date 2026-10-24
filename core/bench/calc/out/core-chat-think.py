import re

def evaluate(expr: str) -> float:
    if not expr.strip():
        raise ValueError("Empty input")
    tokens = tokenize(expr)
    pos = [0]
    
    def parse_expression():
        left = parse_term()
        while pos[0] < len(tokens) and tokens[pos[0]] in ('+', '-'):
            op = tokens[pos[0]]
            pos[0] += 1
            right = parse_term()
            if op == '+':
                left += right
            elif op == '-':
                left -= right
        return left
    
    def parse_term():
        left = parse_power()
        while pos[0] < len(tokens) and tokens[pos[0]] in ('*', '/', '%'):
            op = tokens[pos[0]]
            pos[0] += 1
            right = parse_power()
            if op == '*':
                left *= right
            elif op == '/':
                if right == 0:
                    raise ZeroDivisionError("division by zero")
                left /= right
            elif op == '%':
                if right == 0:
                    raise ZeroDivisionError("modulo by zero")
                left %= right
        return left
    
    def parse_power():
        left = parse_primary()
        while pos[0] < len(tokens) and tokens[pos[0]] == '**':
            pos[0] += 1
            right = parse_power()
            left **= right
        return left
    
    def parse_primary():
        if pos[0] >= len(tokens):
            raise ValueError("Unexpected end of input")
        token = tokens[pos[0]]
        if token == '(':
            pos[0] += 1
            val = parse_expression()
            if pos[0] >= len(tokens) or tokens[pos[0]] != ')':
                raise ValueError("Unbalanced parentheses")
            pos[0] += 1
            return val
        elif token in ('+', '-'):
            sign = token
            pos[0] += 1
            val = parse_primary()
            return -val if sign == '-' else val
        else:
            try:
                num = float(token)
                pos[0] += 1
                return num
            except ValueError:
                raise ValueError(f"Invalid token: {token}")
    
    try:
        result = parse_expression()
        if pos[0] < len(tokens):
            raise ValueError("Extra tokens after expression")
        return result
    except ZeroDivisionError as e:
        raise e
    except Exception as e:
        raise ValueError(str(e)) from e

def tokenize(expr: str) -> list:
    tokens = []
    i = 0
    while i < len(expr):
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c in '+-':
            if i + 1 < len(expr) and (expr[i+1].isdigit() or expr[i+1] == '.'):
                start = i
                i += 1
                while i < len(expr) and (expr[i].isdigit() or expr[i] == '.'):
                    i += 1
                tokens.append(expr[start:i])
            else:
                tokens.append(c)
                i += 1
        elif c in '*/%()':
            if c == '*' and i + 1 < len(expr) and expr[i+1] == '*':
                tokens.append('**')
                i += 2
            else:
                tokens.append(c)
                i += 1
        elif c.isdigit() or c == '.':
            start = i
            if c == '.':
                i += 1
                while i < len(expr) and (expr[i].isdigit() or expr[i] == '.'):
                    i += 1
                tokens.append(expr[start:i])
            else:
                i += 1
                while i < len(expr) and (expr[i].isdigit() or expr[i] == '.'):
                    i += 1
                tokens.append(expr[start:i])
        else:
            raise ValueError(f"Invalid character: {c}")
    return tokens
