export const MIN_SIGNUP_PASSWORD_LENGTH = 8;

export function isSignupPasswordValid(password) {
  return typeof password === 'string' && password.length >= MIN_SIGNUP_PASSWORD_LENGTH;
}
