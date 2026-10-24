import re

class Parser:
    def __init__(self, expr):
        self.tokens = self.tokenize(expr)
        self.pos = 0

    def tokenize(self, expr):
        expr = expr.replace(' ', '')
        tokens = []
        i = 0
        while i < len(expr):
            if expr[i].isdigit() or expr[i] == '.':
                j = i
                if expr[i] == '.':
                    j += 1
                else:
                    while j < len(expr) and (expr[j].isdigit() or expr[j] == '.'):
                        j += 1
                tokens.append(expr[i:j])
                i = j
            elif expr[i] in '+-*/%()^':
                if expr[i] == '-' and (i == 0 or expr[i-1] in '(*-+/^('):
                    tokens.append('U-')
                elif expr[i] == '+':
                    tokens.append('U+')
                else:
                    tokens.append(expr[i])
                i += 1
            else:
                raise ValueError(f"Unknown character: {expr[i]}")
        return tokens

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self):
        self.pos += 1

    def parse(self):
        result = self.parse_expression()
        if self.pos < len(self.tokens):
            raise ValueError("Unexpected token after expression")
        return result

    def parse_expression(self):
        return self.parse_additive()

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.peek() in ['+', '-', 'U+', 'U-']:
            op = self.peek()
            self.consume()
            right = self.parse_multiplicative()
            if op in ['+', 'U+']:
                left += right
            else:
                left -= right
        return left

    def parse_multiplicative(self):
        left = self.parse_power()
        while self.peek() in ['*', '/', '%']:
            op = self.peek()
            self.consume()
            right = self.parse_power()
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

    def parse_power(self):
        left = self.parse_unary()
        while self.peek() == '^':
            self.consume()
            right = self.parse_unary()
            left = pow(left, right)
        return left

    def parse_unary(self):
        if self.peek() == 'U-':
            self.consume()
            return -self.parse_unary()
        elif self.peek() == 'U+':
            self.consume()
            return self.parse_unary()
        else:
            return self.parse_primary()

    def parse_primary(self):
        token = self.peek()
        if token == '(':
            self.consume()
            expr = self.parse_expression()
            if self.peek() != ')':
                raise ValueError("Unbalanced parentheses")
            self.consume()
            return expr
        elif token is not None:
            try:
                return float(token)
            except ValueError:
                raise ValueError(f"Unexpected token: {token}")
        else:
            raise ValueError("Unexpected end of input")

def evaluate(expr: str) -> float:
    try:
        parser = Parser(expr)
        return parser.parse()
    except Exception as e:
        if isinstance(e, ValueError):
            raise e
        else:
            raise ValueError("Invalid expression") from e
